"""Offline contracts for exact public font acquisition, without CI selection edits."""
import hashlib
import io
import json
import lzma
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts import demo_fonts as fonts


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ar_record(name, data):
    header = ('%-16s%-12s%-6s%-6s%-8s%-10s`\n' % (name, 0, 0, 0, '100644', len(data))).encode()
    return header + data + (b'\n' if len(data) % 2 else b'')


def fixture(missing=False, wrong=False, link=False):
    rows, output = [], io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for index, source in enumerate(fonts.FONT_SOURCES):
            data = ('fixture-font-%d' % index).encode()
            row = dict(source, sha256=digest(data), url=fonts.NOTO_URL)
            rows.append(row)
            if missing and index == 0:
                continue
            member = tarfile.TarInfo(row['member'])
            if link and index == 0:
                member.type = tarfile.SYMTYPE
                member.linkname = '/etc/passwd'
            else:
                if wrong and index == 0:
                    data += b'corrupt'
                member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        # Arbitrary package paths are never extracted.
        extra = tarfile.TarInfo('../../outside')
        extra.size = 3
        archive.addfile(extra, io.BytesIO(b'bad'))
    package = b'!<arch>\n' + ar_record('data.tar.xz', lzma.compress(output.getvalue()))
    for row in rows:
        row.update(package_size=len(package), package_sha256=digest(package))
    return tuple(rows), package


class Response(io.BytesIO):
    def geturl(self):
        return fonts.NOTO_URL


class DemoFontsTests(unittest.TestCase):
    def test_source_inventory_matches_exact_four_locked_fonts(self):
        lock = json.loads((REPO / 'templates/demo-timeline-svg/fonts.lock.json').read_text())
        expected = {Path(r['path']).name: r['sha256'] for r in lock['fonts']}
        self.assertEqual(len(fonts.FONT_SOURCES), 4)
        self.assertEqual({r['filename']: r['sha256'] for r in fonts.FONT_SOURCES}, expected)
        self.assertEqual(len({r['url'] for r in fonts.FONT_SOURCES}), 2)
        for row in fonts.FONT_SOURCES:
            self.assertTrue(row['url'].startswith('https://deb.debian.org/debian/pool/main/'))
            self.assertNotIn('?', row['url'])
            self.assertNotIn('@', row['url'])
            self.assertEqual(len(row['package_sha256']), 64)

    def test_default_diagnostics_are_readonly_and_missing(self):
        with patch.object(fonts, '_build_opener', side_effect=AssertionError('network forbidden')):
            self.assertEqual(fonts.diagnose_fonts()['status'], 'MISSING')
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp).resolve()
                absent = base / 'not-created'
                report = fonts.diagnose_fonts(absent)
                self.assertTrue(report['read_only'])
                self.assertFalse(absent.exists())
                self.assertEqual({r['status'] for r in report['fonts']}, {'MISSING'})
                (base / fonts.FONT_SOURCES[0]['filename']).write_bytes(b'wrong same-name font')
                self.assertEqual(fonts.diagnose_fonts(base)['status'], 'FAIL')

    def test_offline_fixture_download_success_only_four_output_files(self):
        rows, package = fixture()
        with tempfile.TemporaryDirectory() as tmp, patch.object(fonts, 'FONT_SOURCES', rows):
            base = Path(tmp).resolve()
            target = base / 'new-fonts'
            with patch.object(fonts, '_build_opener', return_value=Mock(
                    open=Mock(side_effect=lambda *a, **kw: Response(package)))) as download:
                result = fonts.fetch_fonts(target)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(result['status'], 'PASS')
            self.assertFalse(result['read_only'])
            self.assertEqual({p.name for p in base.iterdir()}, {'new-fonts'})
            self.assertEqual({p.name for p in target.iterdir()}, {r['filename'] for r in rows})
            self.assertEqual(fonts.diagnose_fonts(target)['status'], 'PASS')
            (target / rows[0]['filename']).unlink()
            self.assertEqual(fonts.diagnose_fonts(target)['status'], 'MISSING')

    def test_only_new_physical_external_directory_is_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            existing = base / 'existing'
            existing.mkdir()
            sentinel = existing / 'sentinel'
            sentinel.write_bytes(b'unchanged')
            link = base / 'link'
            link.symlink_to(base, target_is_directory=True)
            candidates = [None, '', ' ', existing, link / 'new', base / 'missing/new',
                          REPO / 'forbidden-fonts', Path.home() / 'forbidden-fonts',
                          Path('/usr/share/fonts/forbidden-new')]
            with patch.object(fonts, '_build_opener', side_effect=AssertionError('must reject before network')):
                for target in candidates:
                    with self.subTest(target=target), self.assertRaises(fonts.FontError):
                        fonts.fetch_fonts(target)
            self.assertEqual(sentinel.read_bytes(), b'unchanged')
            self.assertEqual(set(p.name for p in base.iterdir()), {'existing', 'link'})

    def test_bad_package_and_bad_fonts_never_publish_success(self):
        cases = [fixture(missing=True), fixture(wrong=True), fixture(link=True)]
        rows, package = fixture()
        cases.append((rows, package[:-1]))  # Download integrity mismatch.
        cases.append((rows, b'not a Debian package'))
        # Well-hashed transport but malformed container/compression.
        for bad in (b'not ar', b'!<arch>\n', b'!<arch>\n' + ar_record('data.tar.xz', b'bad xz')):
            changed = tuple(dict(r, package_size=len(bad), package_sha256=digest(bad)) for r in rows)
            cases.append((changed, bad))
        for index, (sources, body) in enumerate(cases):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp).resolve() / 'never-published'
                with patch.object(fonts, 'FONT_SOURCES', sources), patch.object(
                        fonts, '_build_opener', return_value=Mock(
                            open=Mock(side_effect=lambda *a, **kw: Response(body)))):
                    with self.assertRaises(fonts.FontError):
                        fonts.fetch_fonts(target)
                self.assertFalse(target.exists())

    def test_download_failure_has_diagnostic_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                fonts, '_build_opener', side_effect=OSError('offline fixture')):
            target = Path(tmp).resolve() / 'new'
            with self.assertRaisesRegex(fonts.FontError, 'download failed.*offline fixture'):
                fonts.fetch_fonts(target)
            self.assertFalse(target.exists())

    def test_redirect_rejected_before_next_request(self):
        handler = fonts._DebianRedirectHandler()
        parent = Mock()
        handler.add_parent(parent)
        request = fonts.urllib.request.Request(fonts.NOTO_URL)
        for url in ('https://github.com/owner/repo', 'http://deb.debian.org/file',
                    'https://deb.debian.org.evil.example/file',
                    'https://deb.debian.org@github.com/file'):
            with self.subTest(url=url), self.assertRaisesRegex(fonts.FontError, 'redirect'):
                handler.http_error_302(request, io.BytesIO(), 302, 'Found', {'location': url})
            parent.open.assert_not_called()
        allowed = handler.redirect_request(request, None, 302, 'Found', {},
                                           'https://deb.debian.org/allowed')
        self.assertEqual(allowed.full_url, 'https://deb.debian.org/allowed')
        with patch.object(fonts.urllib.request, 'install_opener', side_effect=AssertionError('global opener')):
            self.assertTrue(any(isinstance(h, fonts._DebianRedirectHandler)
                                for h in fonts._build_opener().handlers))

    def test_symlink_diagnostics_and_bounded_decompression(self):
        rows, package = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / rows[0]['filename']).symlink_to('/etc/passwd')
            self.assertEqual(fonts.diagnose_fonts(base)['status'], 'FAIL')
        with patch.object(fonts, 'MAX_TAR_BYTES', 64):
            with self.assertRaisesRegex(fonts.FontError, 'oversized'):
                fonts._extract(package, rows)


if __name__ == '__main__':
    unittest.main()
