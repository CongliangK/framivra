"""Synthetic PNG storage tests only: no browser/render/performance claims."""
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

from vtx.render import gfx_cli, gfx_pack
from vtx.runtime import fstxn


def png(seed=0, *, width=32, height=32):
    randomizer = random.Random(24)
    pixels = bytearray(randomizer.randbytes(width * height * 4))
    pixels[0] = seed
    rows = b''.join(b'\0' + pixels[y * width * 4:(y + 1) * width * 4] for y in range(height))
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    return (gfx_cli.PNG_SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 0)) + chunk(b'IEND', b''))


def digest(data):
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(sys.platform == 'linux' and fstxn.write_capability() == 'fd-anchored',
                     'requires Linux fd-anchored storage (no unsafe fallback)')
class GfxPackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.target = self.root / 'page.gfxpack'
        self.frames = [png(i % 6) for i in range(18)]
        self.record = {'schema': 1, 'key': 'synthetic-only', 'width': 32, 'height': 32,
                       'frames': len(self.frames), 'sha256': [digest(f) for f in self.frames],
                       'environment': {'status': 'input-bound', 'test': 'caller-verifies-real-environment'},
                       'dependency_digest': 'd' * 64, 'dependency_enforcement': 'fetch-pinned-local-v1',
                       'dependency_observed': ['file:///fixture.html']}
        self.save_source()

    def save_source(self):
        for i, data in enumerate(self.frames):
            (self.source / f'f{i:04d}.png').write_bytes(data)
        # Deliberately preserve noncanonical original completion bytes verbatim.
        self.marker = json.dumps(self.record, indent=2, ensure_ascii=False).encode() + b'\n'
        (self.source / 'complete.json').write_bytes(self.marker)

    def pack(self, **options):
        return gfx_pack.pack_page(self.source, self.target, expected_record=self.record, root=self.root, **options)

    def header_payload(self):
        data = self.target.read_bytes()
        length, _, _ = gfx_pack._FOOTER.unpack(data[-gfx_pack._FOOTER.size:])
        start = len(data) - gfx_pack._FOOTER.size - length
        return json.loads(data[start:-gfx_pack._FOOTER.size]), data[:start]

    def replace_header(self, change, *, payload=None):
        header, original = self.header_payload()
        change(header)
        encoded = gfx_pack._canonical(header)
        self.target.write_bytes((original if payload is None else payload) + encoded
                                + gfx_pack._FOOTER.pack(len(encoded), hashlib.sha256(encoded).digest(), gfx_pack._END))

    def restore(self, directory=None, **options):
        return gfx_pack.restore_page(self.target, directory or self.root / 'restored',
                                     expected_record=self.record, root=self.root, **options)

    def test_compresses_unique_pngs_deduplicates_and_restores_original_bytes(self):
        stats = self.pack()
        self.assertEqual(stats['unique_pngs'], 6)
        self.assertLess(stats['packed_bytes'], stats['source_png_bytes'] // 2)
        header = gfx_pack.inspect_pack(self.target)
        self.assertEqual(header['record'], self.record)
        unique_size = sum(len(f) for f in set(self.frames))
        self.assertLess(sum(b['bytes'] for b in header['blocks']), unique_size // 2,
                        'compression, not only duplicate-frame removal')
        with gfx_pack.PagePack(self.target, expected_record=self.record) as page:
            for i in (17, 0, 8, 8, 2):
                self.assertEqual(page.read_frame(i), self.frames[i])
            self.assertEqual(page.read_metadata('complete.json'), self.marker)
            with self.assertRaises(gfx_pack.GfxPackError):
                page.read_metadata('../outside')
        restored = self.restore()
        self.assertEqual((restored / 'complete.json').read_bytes(), self.marker)
        for i, data in enumerate(self.frames):
            path = restored / f'f{i:04d}.png'
            self.assertEqual(path.read_bytes(), data)
            self.assertIsNone(gfx_cli.png_invalid_reason(path, width=32, height=32))
        self.assertEqual(len({p.stat().st_ino for p in restored.glob('*.png')}), len(self.frames))
        self.assertEqual((self.source / 'complete.json').read_bytes(), self.marker)
        # Existing production gate still accepts the exact historical certificate.
        self.assertEqual(gfx_cli.require_page_record(restored, len(self.frames), self.record['key'], 32, 32,
                         environment=self.record['environment'], dependency_digest='d' * 64), self.record)

    def test_bounded_blocks_support_nonprefix_frame_reads(self):
        with mock.patch.object(gfx_pack, '_BLOCK_BYTES', 5000):
            self.pack()
        header = gfx_pack.inspect_pack(self.target)
        self.assertGreater(len(header['blocks']), 1)
        with gfx_pack.PagePack(self.target, expected_record=self.record) as page:
            self.assertEqual(page.read_frame(5), self.frames[5])
            self.assertEqual(page._cached[0], header['blobs'][header['frames'][5]]['block'])
        self.assertEqual(gfx_pack.read_frame(self.target, 3, expected_record=self.record), self.frames[3])

    def test_legacy_unknown_pending_or_changed_contract_never_adopted(self):
        for field in ('environment', 'sha256'):
            record = copy.deepcopy(self.record)
            record.pop(field)
            with self.subTest(field=field), self.assertRaises(gfx_pack.GfxPackError):
                gfx_pack.pack_page(self.source, self.target, expected_record=record)
        (self.source / 'complete.json').unlink()
        (self.source / 'environment.pending.json').write_text(json.dumps(self.record))
        with self.assertRaises(gfx_pack.GfxPackError):
            self.pack()
        self.save_source()
        self.pack()
        wrong = copy.deepcopy(self.record)
        wrong['environment']['test'] = 'changed'
        with self.assertRaises(gfx_pack.GfxPackError):
            gfx_pack.read_frame(self.target, 0, expected_record=wrong)
        wrong = copy.deepcopy(self.record)
        wrong['environment']['status'] = 'unknown'
        with self.assertRaises(gfx_pack.GfxPackError):
            gfx_pack.read_frame(self.target, 0, expected_record=wrong)

    def test_pack_corruption_and_png_corruption_fail_before_target_publication(self):
        self.target.write_bytes(b'old-target')
        (self.source / 'f0000.png').write_bytes(self.frames[1])
        with self.assertRaises(gfx_pack.GfxPackError):
            self.pack()
        self.assertEqual(self.target.read_bytes(), b'old-target')
        self.save_source()
        self.pack()
        original = self.target.read_bytes()
        for damaged in (original[:-1], original + b'trailing', original[:20] + bytes([original[20] ^ 1]) + original[21:]):
            self.target.write_bytes(damaged)
            with self.subTest(length=len(damaged)), self.assertRaises(gfx_pack.GfxPackError):
                self.restore()
            self.assertFalse((self.root / 'restored').exists())
        self.assertEqual(list(self.root.glob('.*.gfx-pack.tmp')), [])

    def test_malicious_index_members_counts_and_budgets_rejected(self):
        changes = [lambda h: h['metadata'][0].update(name='../escape'),
                   lambda h: h['frames'].append(0),
                   lambda h: h['frames'].__setitem__(0, True),
                   lambda h: h['blocks'][0].update(raw_bytes=10**15),
                   lambda h: h['blobs'][0].update(bytes=10**15),
                   lambda h: h['blobs'][0].update(offset=1),
                   lambda h: h['frames'].__setitem__(0, h['frames'][1])]
        for change in changes:
            self.pack()
            self.replace_header(change)
            with self.subTest(change=change), self.assertRaises(gfx_pack.GfxPackError):
                self.restore()
        self.pack()
        with self.assertRaises(gfx_pack.GfxPackError):
            gfx_pack.read_frame(self.target, 0, expected_record=self.record, max_frame_bytes=100)
        with self.assertRaises(gfx_pack.GfxPackError):
            gfx_pack.inspect_pack(self.target, max_header_bytes=10)

    def test_zlib_bombs_truncation_and_trailing_streams_rejected_even_with_updated_digest(self):
        for transform in (lambda c: c[:-2], lambda c: c + b'junk',
                          lambda c: c + zlib.compress(b'junk'), lambda c: zlib.compress(b'0' * 100000)):
            self.pack()
            header, prefix = self.header_payload()
            block = header['blocks'][0]
            compressed = transform(prefix[block['offset']:block['offset'] + block['bytes']])
            block.update(bytes=len(compressed), sha256=digest(compressed))
            payload = gfx_pack._MAGIC + compressed
            encoded = gfx_pack._canonical(header)
            self.target.write_bytes(payload + encoded + gfx_pack._FOOTER.pack(len(encoded), hashlib.sha256(encoded).digest(), gfx_pack._END))
            with self.subTest(transform=transform), self.assertRaises(gfx_pack.GfxPackError):
                self.restore()
            self.assertFalse((self.root / 'restored').exists())

    def test_input_callback_aborts_pack_and_restore(self):
        def fail():
            raise RuntimeError('input identity changed')
        self.target.write_bytes(b'previous')
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            self.pack(verify_inputs=fail)
        self.assertEqual(self.target.read_bytes(), b'previous')
        self.pack()
        with self.assertRaises(RuntimeError):
            self.restore(verify_inputs=fail)
        self.assertFalse((self.root / 'restored').exists())

    def test_symlinks_hardlinks_specials_and_parent_alias_rejected(self):
        self.pack()
        restored = self.root / 'restored'
        outside = self.root / 'outside'
        outside.mkdir()
        restored.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(gfx_pack.GfxPackError):
            self.restore()
        self.assertEqual(list(outside.iterdir()), [])
        restored.unlink()
        alias = self.root / 'alias'
        alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(gfx_pack.GfxPackError):
            self.restore(alias / 'page')
        frame = self.source / 'f0000.png'
        frame.unlink()
        frame.symlink_to(self.source / 'f0001.png')
        with self.assertRaises(gfx_pack.GfxPackError):
            self.pack()
        frame.unlink()
        os.link(self.source / 'f0001.png', frame)
        with self.assertRaises(gfx_pack.GfxPackError):
            self.pack()
        frame.unlink()
        os.mkfifo(frame)
        with self.assertRaises(gfx_pack.GfxPackError):
            self.pack()

    def test_existing_lock_only_page_preserves_lock_inodes_and_commits_marker_last(self):
        self.pack()
        restored = self.root / 'restored'
        restored.mkdir()
        lock = restored / 'f0000.png.lock'
        lock.write_bytes(b'protected')
        inode = lock.stat().st_ino
        renamed = []
        original = gfx_pack._rename_new
        def record_rename(parent, source, target, **options):
            renamed.append(target)
            if target != 'complete.json':
                self.assertFalse((restored / 'complete.json').exists())
            return original(parent, source, target, **options)
        with mock.patch.object(gfx_pack, '_rename_new', side_effect=record_rename):
            self.restore()
        self.assertEqual(renamed[-1], 'complete.json')
        for i, data in enumerate(self.frames):
            self.assertEqual((restored / f'f{i:04d}.png').read_bytes(), data)
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertEqual(lock.read_bytes(), b'protected')
        with self.assertRaises(gfx_pack.GfxPackError):
            self.restore()

    def test_existing_lock_only_publish_failure_never_publishes_complete(self):
        self.pack()
        restored = self.root / 'restored'
        restored.mkdir()
        lock = restored / 'page.lock'
        lock.touch()
        original = gfx_pack._rename_new
        count = 0
        def fail(parent, source, target, **options):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError('injected publication interruption')
            return original(parent, source, target, **options)
        with mock.patch.object(gfx_pack, '_rename_new', side_effect=fail), self.assertRaises(OSError):
            self.restore()
        self.assertFalse((restored / 'complete.json').exists())
        self.assertTrue(lock.exists())
        self.assertEqual(len(list(restored.glob('*.png'))), 2)

    def test_existing_nonlock_or_linked_lock_is_rejected(self):
        self.pack()
        for name in ('unexpected', 'link.lock'):
            restored = self.root / name
            restored.mkdir()
            if name == 'unexpected':
                (restored / 'payload.png').touch()
            else:
                (restored / 'payload.lock').symlink_to(self.target)
            with self.assertRaises(gfx_pack.GfxPackError):
                self.restore(restored)
            self.assertFalse((restored / 'complete.json').exists())

    def test_svg_and_fixed_electron_sidecars_preserve_bytes_without_claiming_eligibility(self):
        self.record = {'format': 'framivra-svg-completion/v1', 'key': 'svg-test',
                       'input': {'frame_count': len(self.frames), 'width': 32, 'height': 32},
                       'sha256': [digest(f) for f in self.frames]}
        self.save_source()
        self.pack()
        self.assertEqual(self.restore().joinpath('complete.json').read_bytes(), self.marker)
        run_id = 'a' * 32
        self.record = {'schema': 1, 'key': 'electron-test', 'width': 32, 'height': 32,
                       'frames': len(self.frames), 'sha256': [digest(f) for f in self.frames],
                       'backend_identity': 'caller-verified-electron', 'render_runs':
                       [{'run_id': run_id, 'frames': list(range(len(self.frames)))}]}
        self.save_source()
        evidence = self.source / '.electron' / run_id
        evidence.mkdir(parents=True)
        for name in ('request.json', 'adapter-result.json', 'result.json'):
            (evidence / name).write_bytes(b'{"opaque_original":"absolute-path-retained"}\n')
        (evidence / 'not-carried.txt').write_text('not part of fixed proof')
        self.pack()
        restored = self.restore(self.root / 'electron')
        for name in ('request.json', 'adapter-result.json', 'result.json'):
            self.assertEqual((restored / '.electron' / run_id / name).read_bytes(), (evidence / name).read_bytes())
        self.assertFalse((restored / '.electron' / run_id / 'not-carried.txt').exists())

    def test_raced_restore_target_is_not_replaced_or_followed(self):
        self.pack()
        restored = self.root / 'restored'
        outside = self.root / 'outside'
        outside.mkdir()
        original = gfx_pack._rename_new
        def replace_target(parent, source, target, **options):
            restored.symlink_to(outside, target_is_directory=True)
            return original(parent, source, target, **options)
        with mock.patch.object(gfx_pack, '_rename_new', side_effect=replace_target), self.assertRaises(OSError):
            self.restore()
        self.assertTrue(restored.is_symlink())
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(list(self.root.glob('.*.gfx-pack.tmp')), [])

    def test_inplace_pack_mutation_is_detected_by_open_snapshot(self):
        self.pack()
        with gfx_pack.PagePack(self.target, expected_record=self.record) as page:
            page.verify()
            data = self.target.read_bytes()
            self.target.write_bytes(data)
            with self.assertRaisesRegex(gfx_pack.GfxPackError, 'changed'):
                page.read_frame(0)
            # __exit__ also refuses to certify a changed file.
            with self.assertRaises(gfx_pack.GfxPackError):
                page.__exit__(None, None, None)

    def test_invalid_png_is_rejected_even_when_hash_matches_expected(self):
        self.frames[0] = self.frames[0][:-1] + b'X'
        self.record['sha256'][0] = digest(self.frames[0])
        self.save_source()
        with self.assertRaisesRegex(gfx_pack.GfxPackError, 'invalid PNG'):
            self.pack()
        self.assertFalse(self.target.exists())

    def test_nonanchored_platform_has_explicit_failure_without_writes(self):
        with mock.patch.object(fstxn, 'write_capability', return_value='legacy'):
            with self.assertRaisesRegex(gfx_pack.GfxPackError, 'fd-anchored'):
                self.pack()
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
