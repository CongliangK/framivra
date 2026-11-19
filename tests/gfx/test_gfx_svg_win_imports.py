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
_NAME, _TABLE, _ORDINAL = 1112, 1152, 1 << 63


def _sample(ordinary=(), delay=(), *, symbols=False):
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
    cursor, thunk = 1024, _TABLE
    if symbols:
        spelling = b'?Mixed@Name/with\\Punctuation\0'
        data[_RAW + _NAME + 2:_RAW + _NAME + 2 + len(spelling)] = spelling
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
            if symbols:
                if index == 1:
                    pack_into('<I', data, row, _RVA + thunk)
                    pack_into('<I', data, row + 16, _RVA + thunk + 24)
                else:
                    pack_into('<III', data, row + 8, _RVA + thunk + 48, _RVA + thunk + 24, _RVA + thunk)
                pack_into('<QQ', data, _RAW + thunk, _RVA + _NAME, _ORDINAL | 65535)
                # Separate IAT contains opaque values, not valid import thunks.
                pack_into('<QQ', data, _RAW + thunk + 24, 0x1_0000_0000, 0x2_0000_0000)
                thunk += 56
            data[_RAW + cursor:_RAW + cursor + len(name)] = name
            cursor += len(name) + 1
    return data


def _parse(data, *, budget=None):
    return pe._declarations(lambda start, length: data[start:start + length], len(data), symbol_budget=budget)


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


class PeSymbolMetadata(unittest.TestCase):
    def test_lookup_and_rva_delay_use_only_declarations(self):
        data = _sample((b'A.DLL',), (b'B.dll',), symbols=True)
        pack_into('<I', data, _RAW + 4, 0xffffffff)  # independent OFT despite binding metadata
        pack_into('<II', data, _OPT + 200, _RVA + 1392, 16)
        pack_into('<II', data, _OPT + 208, _RVA + _TABLE + 24, 24)
        pack_into('<Q', data, _RAW + _TABLE + 56 + 8, _ORDINAL)  # low 16 bits can be zero
        result = _parse(data, budget=pe._SymbolBudget())
        spelling = {'name': '?Mixed@Name/with\\Punctuation'}
        self.assertEqual(result, {'ordinary': {'a.dll': [spelling, {'ordinal': 65535}]},
                                  'delay': {'b.dll': [spelling, {'ordinal': 0}]}})
        self.assertEqual(_parse(data), {'ordinary': ['a.dll'], 'delay': ['b.dll']})

    def test_only_unbound_first_thunk_fallback(self):
        data = _sample((b'a.dll',), symbols=True)
        pack_into('<I', data, _RAW, 0)
        pack_into('<QQ', data, _RAW + _TABLE + 24, _ORDINAL | 37, _RVA + _NAME)
        self.assertEqual(_parse(data, budget=pe._SymbolBudget())['ordinary']['a.dll'],
                         [{'ordinal': 37}, {'name': '?Mixed@Name/with\\Punctuation'}])
        for field in (_RAW + 4, _RAW + 8, _OPT + 200, _OPT + 204):
            rejected = data[:]
            value = _RVA + 1392 if field == _OPT + 200 else 8
            pack_into('<I', rejected, field, value)
            # Make the bound-import directory well formed for its primary case.
            if field == _OPT + 200:
                pack_into('<I', rejected, field + 4, 16)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'bound FirstThunk|invalid or unaligned'):
                _parse(rejected, budget=pe._SymbolBudget())

    def test_thunk_modes_raw_limits_and_overlap(self):
        for field, fmt, value, message in (
                (_RAW, '<I', _RVA + _TABLE + 1, 'unaligned thunk'),
                (_RAW, '<I', _RVA + 2048, 'one readable raw'),
                (_RAW, '<I', _RVA + 2040, 'no bounded terminator'),
                (_RAW + 16, '<I', _RVA + _TABLE, 'overlapping import table'),
                (_RAW + _TABLE + 8, '<Q', _ORDINAL | (1 << 16), 'reserved bits'),
                (_RAW + _TABLE, '<Q', 1 << 31, 'import-by-name RVA'),
                (_RAW + _TABLE, '<Q', 1 << 32, 'import-by-name RVA'),
                (_RAW + _TABLE, '<Q', _RVA + _NAME + 1, 'import-by-name RVA'),
                (_RAW + _TABLE, '<Q', _RVA + _TABLE + 24, 'non-overlapping terminator'),
                (_RAW + _TABLE + 8, '<Q', _RVA + _NAME + 4, 'overlapping import-by-name'),
                (_RAW + _TABLE + 24, '<Q', 0, 'IAT terminates before'),
                (_RAW + _TABLE + 40, '<Q', 1, 'IAT has no matching terminator'),
                (_RAW + 512 + 16, '<I', 0, 'missing or unaligned thunk'),
                (_RAW + 512 + 20, '<I', _RVA + 1400, 'unsupported delay binding'),
                (_RAW + 512 + 24, '<I', _RVA + 1400, 'unsupported delay binding')):
            data = _sample((b'a.dll',), (b'b.dll',), symbols=True)
            pack_into('<Q', data, _RAW + 2040, _ORDINAL | 4)
            pack_into(fmt, data, field, value)
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, message):
                _parse(data, budget=pe._SymbolBudget())

    def test_name_and_cumulative_budgets(self):
        data = _sample((b'a.dll',), symbols=True)
        for spelling, message in ((b'\0', 'invalid symbol name'), (b'\xff', 'invalid symbol name')):
            bad = data[:]
            bad[_RAW + _NAME + 2:_RAW + _NAME + 3] = spelling
            with self.assertRaisesRegex(ValueError, message):
                _parse(bad, budget=pe._SymbolBudget())
        for attr, limit, message in (('_MAX_SYMBOL_NAME', 4, 'no bounded non-overlapping terminator'),
                                     ('_SYMBOL_READ_BYTES', 400, 'metadata exceeds its read budget'),
                                     ('_MAX_THUNKS', 1, 'member thunk budget'),
                                     ('_MAX_NAME_TEXT', 4, 'member symbol name budget'),
                                     ('_PACKAGE_READS', 1, 'package read budget'),
                                     ('_PACKAGE_NAME_TEXT', 4, 'package output budget'),
                                     ('_SYMBOL_SECONDS', 0, 'metadata time budget')):
            with self.subTest(limit=attr), mock.patch.object(pe, attr, limit), self.assertRaisesRegex(ValueError, message):
                _parse(data, budget=pe._SymbolBudget())
        budget = pe._SymbolBudget()
        with mock.patch.object(pe, '_PACKAGE_THUNKS', 3):
            _parse(data, budget=budget)
            with self.assertRaisesRegex(ValueError, 'package output budget'):
                _parse(data, budget=budget)


@unittest.skipUnless(sys.platform == 'win32' and sysconfig.get_platform() == 'win-amd64', 'Windows x64 pins')
class VerifiedPeDeclarationSnapshot(unittest.TestCase):
    def test_authentication_precedes_same_handle_inventory_and_cleanup(self):
        leaves = {'bin/inkscape.com': _sample((b'MIXED.dll', b'API-MS-test.dll'), (b'outside.drv',), symbols=True),
                  'bin/Mixed.DLL': _sample((b'inkscape.COM',), (b'libfontconfig-1.dll',), symbols=True),
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
                symbols = pe._inspect_package_imports(root, scratch=scratch, include_symbols=True)
                self.assertEqual(symbols['schema'], 'framivra/windows-svg-pe-declarations/2')
                symbols['schema'] = 'framivra/windows-svg-pe-declarations/1'
                for member in symbols['members'].values():
                    for edge in member['ordinary'] + member['delay']:
                        self.assertEqual(edge.pop('symbols'), [{'name': '?Mixed@Name/with\\Punctuation'},
                                                               {'ordinal': 65535}])
                self.assertEqual(symbols, result)
                self.assertEqual(list(scratch.iterdir()), [])
                with mock.patch.object(pe, '_DLLS', {}), self.assertRaisesRegex(ValueError, 'fixed DLL profile'):
                    pe._inspect_package_imports(root, scratch=scratch)
                self.assertEqual(list(scratch.iterdir()), [])
                (vendor / 'bin/unreached.exe').write_bytes(b'NOT declared')  # unchanged shape, bad bytes
                with mock.patch.object(pe, '_package_declarations', side_effect=AssertionError('parsed before digest')):
                    with self.assertRaisesRegex(ValueError, 'tree identity mismatch'):
                        pe._inspect_package_imports(root, scratch=scratch)
                self.assertEqual(list(scratch.iterdir()), [])
