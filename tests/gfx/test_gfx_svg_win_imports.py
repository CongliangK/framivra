"""Inert declaration records; no executable code, loader, or system file lookup."""
import contextlib
from hashlib import sha256
import json
from pathlib import Path
from struct import pack_into
import sys
import sysconfig
import tempfile
import unittest
from unittest import mock

from vtx.render import gfx_svg_win_imports as pe
from vtx.render import gfx_svg_win_snapshot as snapshot


_OPT, _SECTION, _RAW, _RVA = 88, 328, 512, 4096


def _sample(ordinary=(), delay=()):
    data = bytearray(_RAW + 2048)
    data[:2] = b'MZ'
    pack_into('<I', data, 60, 64)
    pack_into('<4sHHIIIHH', data, 64, b'PE\0\0', 0x8664, 1, 0, 0, 0, 240, 2)
    pack_into('<H', data, _OPT, 0x20b)
    pack_into('<II', data, _OPT + 32, 4096, 512)
    pack_into('<II', data, _OPT + 56, 8192, _RAW)
    pack_into('<I', data, _OPT + 108, 16)
    pack_into('<8sIIII', data, _SECTION, b'.rdata', 2048, _RVA, 2048, _RAW)
    pack_into('<I', data, _SECTION + 36, 0x40000000)
    cursor = 1024
    for index, names, table, span, width in ((1, ordinary, 0, 512, 20), (13, delay, 512, 512, 32)):
        if not names:
            continue
        pack_into('<II', data, _OPT + 112 + 8 * index, _RVA + table, span)
        for i, name in enumerate(names):
            row = _RAW + table + i * width
            if index == 1:
                pack_into('<I', data, row + 12, _RVA + cursor)
            else:
                pack_into('<II', data, row, 1, _RVA + cursor)
            data[_RAW + cursor:_RAW + cursor + len(name)] = name
            cursor += len(name) + 1
    return data


def _parse(data):
    return pe._declarations(lambda start, length: data[start:start + length], len(data))


class PeDeclarationMetadata(unittest.TestCase):
    def test_ordinary_delay_and_declared_span(self):
        self.assertEqual(_parse(_sample()), {'ordinary': [], 'delay': []})
        data = _sample((b'Mixed.DLL', b'WinSpool.DRV'), (b'ext-ms-test.dll',))
        self.assertEqual(_parse(data), {'ordinary': ['mixed.dll', 'winspool.drv'],
                                       'delay': ['ext-ms-test.dll']})

    def test_rejects_bad_header_directory_and_raw_mapping(self):
        for field, value, message in (
                (68, 0x014c, 'AMD64'), (_OPT, 0x010b, r'PE32\+'),
                (_OPT + 108, 17, r'PE32\+'), (_OPT + 60, 128, 'layout'),
                (_SECTION + 20, 1024, 'raw section range'), (_SECTION + 36, 0, 'one readable raw'),
                (_OPT + 120, 128, 'one readable raw'), (_OPT + 124, 0, 'invalid import directory'),
                (_OPT + 124, 2049, 'one readable raw'), (_OPT + 216, _RVA, 'overlapping import'),
                (_RAW + 12, _RVA + 2048, 'one readable raw'),
                (_RAW + 512, 0, 'unsupported delay')):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, message):
                data = _sample((b'a.dll',), (b'b.dll',))
                pack_into('<H' if field in (68, _OPT) else '<I', data, field, value)
                _parse(data)

    def test_rejects_overlap_even_with_non_readable_section(self):
        for vaddr, raw_offset in ((_RVA, 2560), (2 * _RVA, _RAW)):
            data = _sample((b'a.dll',)) + bytes(512)
            pack_into('<H', data, 70, 2)
            pack_into('<I', data, _OPT + 56, 12288)
            pack_into('<IIII', data, _SECTION + 48, 512, vaddr, 512, raw_offset)
            with self.subTest(vaddr=vaddr, raw_offset=raw_offset), self.assertRaisesRegex(ValueError, 'overlapping section'):
                _parse(data)

    def test_rejects_invalid_duplicate_and_truncated_names(self):
        for name in (b'', b'C:\\x.dll', b'../x.dll', b'CON.dll', b'a\xff.dll', b'a.dll '):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'invalid module name'):
                _parse(_sample((name,)))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            _parse(_sample((b'Mixed.DLL', b'mixed.dll')))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            _parse(_sample((b'Mixed.DLL',), (b'mixed.dll',)))
        data = _sample((b'prefix.dll', b'unused.dll'))
        pack_into('<I', data, _RAW + 32, _RVA + 1025)  # two on-disk string spans overlap
        with self.assertRaisesRegex(ValueError, 'overlapping module declaration'):
            _parse(data)
        data = _sample((b'a.dll',))
        data[-5:] = b'a.dll'
        pack_into('<I', data, _RAW + 12, _RVA + 2043)
        with self.assertRaisesRegex(ValueError, 'no bounded terminator'):
            _parse(data)

    def test_budgets_and_short_read(self):
        data = _sample((b'a.dll',), (b'b.dll',))
        for name, limit, message in (('_MAX_READS', 5, 'read budget'), ('_MAX_BYTES', 800, 'read budget'),
                                     ('_MAX_DESCRIPTORS', 1, 'no bounded terminator')):
            with self.subTest(name=name), mock.patch.object(pe, name, limit), self.assertRaisesRegex(ValueError, message):
                _parse(data)
        with self.assertRaisesRegex(ValueError, 'was truncated'):
            pe._declarations(lambda offset, length: data[offset:offset + length - 1], len(data))


@unittest.skipUnless(sys.platform == 'win32' and sysconfig.get_platform() == 'win-amd64', 'Windows x64 pins')
class VerifiedPeDeclarationSnapshot(unittest.TestCase):
    def test_authentication_precedes_same_handle_inventory_and_cleanup(self):
        leaves = {'bin/inkscape.com': _sample((b'MIXED.dll', b'API-MS-test.dll'), (b'outside.drv',)),
                  'bin/Mixed.DLL': _sample((b'inkscape.COM',), (b'libfontconfig-1.dll',)),
                  'bin/libfontconfig-1.dll': _sample(), 'bin/api-ms-test.dll': b'not a PE candidate',
                  'bin/unreached.exe': b'not declared', 'sub/outside.drv': b'not in bin'}
        files = {name: [len(data), sha256(data).hexdigest()] for name, data in leaves.items()}
        manifest = {'schema': 'framivra-audit/inkscape-vendor-tree/1', 'directories': ['bin', 'sub'], 'files': files}
        identity = sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()
        expected_fontconfig = {pe._FONTCONFIG: tuple(files['bin/' + pe._FONTCONFIG])}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vendor, scratch = root / 'inkscape', root / 'scratch'
            for directory in (vendor / 'bin', vendor / 'sub', scratch):
                directory.mkdir(parents=True)
            for name, data in leaves.items():
                (vendor / name).write_bytes(data)
            original_digest, original_api = snapshot._image_digest, snapshot._windows_api
            authenticated, sought = set(), set()

            def digest(handle, kernel, size):
                result = original_digest(handle, kernel, size)
                authenticated.add(handle)
                return result

            def api(name, *args):
                fn = original_api(name, *args)
                if name != 'SetFilePointerEx':
                    return fn

                def seek(handle, *seek_args):
                    self.assertIn(handle, authenticated)
                    sought.add(handle)
                    return fn(handle, *seek_args)

                return seek

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.multiple(snapshot, _MAX_FILES=len(files), _MAX_DIRS=2,
                                                        _MAX_BYTES=sum(x[0] for x in files.values()),
                                                        _TREE_SHA256=identity, _image_digest=digest, _windows_api=api))
                stack.enter_context(mock.patch.object(pe, '_DLLS', expected_fontconfig))
                result = pe._inspect_package_imports(root, scratch=scratch)
                self.assertEqual(len(sought), 3)
                self.assertEqual(result['roots'], {'bin/inkscape.com': ['bin/Mixed.DLL', 'bin/inkscape.com',
                                                                      'bin/libfontconfig-1.dll'],
                                                  'bin/libfontconfig-1.dll': ['bin/libfontconfig-1.dll']})
                entry = result['members']['bin/inkscape.com']
                self.assertEqual(entry['ordinary'], [{'name': 'api-ms-test.dll', 'kind': 'unresolved_api_set'},
                                                     {'name': 'mixed.dll', 'kind': 'package_candidate',
                                                      'member': 'bin/Mixed.DLL'}])
                self.assertEqual(entry['delay'], [{'name': 'outside.drv', 'kind': 'unresolved_external'}])
                self.assertEqual(list(scratch.iterdir()), [])
                with mock.patch.object(pe, '_DLLS', {}), self.assertRaisesRegex(ValueError, 'fixed DLL profile'):
                    pe._inspect_package_imports(root, scratch=scratch)
                self.assertEqual(list(scratch.iterdir()), [])
                (vendor / 'bin/unreached.exe').write_bytes(b'NOT declared')  # unchanged shape, bad bytes
                with mock.patch.object(pe, '_package_declarations', side_effect=AssertionError('parsed before digest')):
                    with self.assertRaisesRegex(ValueError, 'tree identity mismatch'):
                        pe._inspect_package_imports(root, scratch=scratch)
                self.assertEqual(list(scratch.iterdir()), [])
