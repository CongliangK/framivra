"""Focused managed-font safety/registration contracts; no network or global writes.

Linux discovery is mocked here; a separate real fontconfig run validates the
pinned bytes. macOS and Windows tests are contracts, NOT physical-platform tests.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock
import urllib.request
import xml.etree.ElementTree as ET

from scripts import install_runtime_fonts as fonts


def source(name, data, kind='font'):
    return dict(filename=name, sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                kind=kind, url='https://raw.githubusercontent.com/owner/repo/' + 'a' * 40 + '/' + name)


class RuntimeFonts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'runtime & fonts'
        self.home = self.base / 'home'
        self.payload = {'Example.ttf': b'pinned font fixture', 'Example-OFL.txt': b'pinned notice fixture'}
        self.sources = (dict(source('Example.ttf', self.payload['Example.ttf']), license_file='Example-OFL.txt'),
                        source('Example-OFL.txt', self.payload['Example-OFL.txt'], 'license'))
        self.patch = mock.patch.object(fonts, 'SOURCES', self.sources)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        network = mock.patch.object(fonts, 'download', side_effect=lambda s, **kwargs: self.payload[s['filename']])
        self.network = network.start()
        self.addCleanup(network.stop)
        command = mock.patch.object(fonts, 'run', side_effect=lambda args, env:
            str(self.root / 'fonts' / 'Example.ttf') + '\n' if args[0] == 'fc-list' else '')
        self.command = command.start()
        self.addCleanup(command.stop)

    def install(self, **kwargs):
        return fonts.install(self.root, system=kwargs.pop('system', 'linux'),
                             home=self.home, environment=kwargs.pop('environment', {}), **kwargs)

    def test_new_linux_install_and_offline_rerun_preserve_bytes(self):
        report = self.install()
        self.assertEqual(report['environment'], {'FONTCONFIG_FILE': str(self.root / 'fonts/fonts.conf')})
        self.assertFalse(self.home.exists())
        config = ET.fromstring((self.root / 'fonts/fonts.conf').read_bytes())
        self.assertEqual(config.find('dir').text, str(self.root / 'fonts'))
        self.assertEqual(config.find('include').text, '/etc/fonts/fonts.conf')
        self.assertEqual(config.find('cachedir').text, str(self.root / 'font-cache'))
        for call in self.command.call_args_list:
            self.assertEqual(call.args[1]['FONTCONFIG_FILE'], report['environment']['FONTCONFIG_FILE'])
        files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.root / 'fonts').iterdir()}
        self.network.reset_mock()
        self.network.side_effect = AssertionError('rerun must not download')
        second = self.install()
        self.assertEqual(report, second)
        self.assertEqual(files, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files})
        manifest = json.loads((self.root / 'fonts/sources.json').read_text())
        self.assertEqual(manifest['sources'][0]['license_file'], 'Example-OFL.txt')

    def test_verified_local_font_and_notice_need_no_download(self):
        local = self.base / 'local'
        local.mkdir()
        for name, data in self.payload.items():
            (local / name).write_bytes(data)
        self.network.side_effect = AssertionError('matching local bytes need no download')
        with mock.patch.object(fonts, 'local_candidates', side_effect=lambda s, *_: [local / s['filename']]):
            self.install()
        self.network.assert_not_called()
        self.assertEqual((self.root / 'fonts/Example.ttf').read_bytes(), self.payload['Example.ttf'])

    def test_wrong_cached_sha_is_not_replaced_or_downloaded(self):
        destination = self.root / 'fonts'
        destination.mkdir(parents=True)
        target = destination / 'Example.ttf'
        target.write_bytes(b'different font')
        with self.assertRaisesRegex(fonts.FontError, 'SHA256'):
            self.install()
        self.assertEqual(target.read_bytes(), b'different font')
        self.network.assert_not_called()
        self.assertEqual(list(destination.iterdir()), [target])

    def test_wrong_download_sha_leaves_destination_absent(self):
        self.network.side_effect = lambda s, **kwargs: b'corrupt download'
        with self.assertRaisesRegex(fonts.FontError, 'SHA256'):
            self.install()
        self.assertFalse(self.root.exists())

    def test_bad_paths_and_source_names_do_not_write(self):
        with self.assertRaises(fonts.FontError):
            fonts.install(self.base / 'elsewhere' / '..' / 'target', system='linux')
        for name in ('../outside.ttf', '/absolute.ttf', r'C:\font.ttf', 'file:stream.ttf', '..', ''):
            with self.subTest(name=name), mock.patch.object(fonts, 'SOURCES', (dict(self.sources[0], filename=name),)):
                with self.assertRaises(fonts.FontError):
                    self.install()
        self.assertFalse(self.root.exists())
        self.network.assert_not_called()

    def test_symlink_root_and_font_rejected(self):
        actual = self.base / 'actual'
        actual.mkdir()
        link = self.base / 'link'
        link.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(fonts.FontError):
            fonts.install(link, system='linux')
        destination = self.root / 'fonts'
        destination.mkdir(parents=True)
        (destination / 'Example.ttf').symlink_to(actual / 'missing.ttf')
        with self.assertRaises(fonts.FontError):
            self.install()
        self.assertEqual(list(actual.iterdir()), [])
        self.network.assert_not_called()

    def test_windows_reparse_ancestor_rejected_without_python312(self):
        self.root.mkdir()
        original = Path.lstat
        def reparse(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == self.root:
                return types.SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
            return result
        with mock.patch.object(Path, 'lstat', reparse), self.assertRaisesRegex(fonts.FontError, 'junction'):
            self.install()
        self.network.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_linux_missing_discovery_is_failure_not_success(self):
        self.command.return_value = ''
        self.command.side_effect = None
        with self.assertRaisesRegex(fonts.FontError, 'not visible'):
            self.install()
        self.assertFalse((self.root / 'fonts/sources.json').exists())

    def test_existing_fontconfig_is_not_overwritten(self):
        destination = self.root / 'fonts'
        destination.mkdir(parents=True)
        config = destination / 'fonts.conf'
        config.write_bytes(b'existing local configuration')
        with self.assertRaises(fonts.FontError):
            self.install()
        self.assertEqual(config.read_bytes(), b'existing local configuration')
        self.command.assert_not_called()

    def test_macos_user_font_copies_and_notice_contract(self):
        report = self.install(system='darwin')
        directory = self.home / 'Library/Fonts'
        self.assertEqual(report['user_fonts_directory'], str(directory))
        self.assertEqual(report['environment'], {})
        for name, data in self.payload.items():
            self.assertEqual((directory / name).read_bytes(), data)
        self.install(system='darwin')
        self.command.assert_not_called()

    def test_conflicting_user_font_rejected_before_download_or_root_write(self):
        directory = self.home / 'Library/Fonts'
        directory.mkdir(parents=True)
        target = directory / 'Example.ttf'
        target.write_bytes(b'personal font')
        with self.assertRaises(fonts.FontError):
            self.install(system='darwin')
        self.assertEqual(target.read_bytes(), b'personal font')
        self.assertFalse(self.root.exists())
        self.network.assert_not_called()

    def test_windows_user_scope_and_missing_localappdata_contract(self):
        with self.assertRaisesRegex(fonts.FontError, 'LOCALAPPDATA'):
            self.install(system='win32')
        local = self.home / 'AppData/Local'
        directory = local / 'Microsoft/Windows/Fonts'
        with mock.patch.object(fonts, 'register_windows') as register:
            report = self.install(system='win32', environment={'LOCALAPPDATA': str(local)})
        register.assert_called_once_with([directory / 'Example.ttf'])
        self.assertEqual(report['user_fonts_directory'], str(directory))
        self.assertEqual((directory / 'Example-OFL.txt').read_bytes(), self.payload['Example-OFL.txt'])
        self.command.assert_not_called()


class SourceAndRegistrationContracts(unittest.TestCase):
    def test_all_sources_have_real_pins_and_notices(self):
        names = {s['filename'] for s in fonts.SOURCES}
        for entry in fonts.SOURCES:
            self.assertRegex(entry['sha256'], r'^[a-f0-9]{64}$')
            self.assertEqual(fonts.filename(entry['filename']), entry['filename'])
            if entry['kind'] == 'font':
                self.assertIn(entry['license_file'], names)
            if 'package_sha256' not in entry:
                self.assertRegex(entry['url'], r'https://raw.githubusercontent.com/[^/]+/[^/]+/[a-f0-9]{40}/')
        self.assertEqual([s['sha256'] for s in fonts.SOURCES[:4]],
                         [s['sha256'] for s in fonts.demo_fonts.FONT_SOURCES])

    def test_redirect_and_bounded_download_reject_wrong_bytes(self):
        handler = fonts.PinnedRedirect()
        request = urllib.request.Request('https://raw.githubusercontent.com/a/b/font.ttf')
        for url in ('http://raw.githubusercontent.com/a/b', 'https://evil.example/font.ttf',
                    'https://raw.githubusercontent.com.evil.example/font.ttf'):
            with self.subTest(url=url), self.assertRaises(fonts.FontError):
                handler.redirect_request(request, None, 302, '', {}, url)
        entry = source('font.ttf', b'font')
        cached = mock.Mock()
        cached.is_file.return_value = True
        cached.stat.return_value.st_size = entry['size']
        response = mock.MagicMock()
        response.read.return_value = b'wrong'
        cached.open.return_value.__enter__ = mock.Mock(return_value=response)
        cached.open.return_value.__exit__ = mock.Mock(return_value=False)
        with mock.patch.object(fonts.install_downloads, 'acquire', return_value=cached), \
             mock.patch.object(fonts, 'physical', return_value=cached):
            with self.assertRaises(fonts.FontError):
                fonts.download(entry, cache=Path('/fixture/cache'))
        response.read.assert_called_once_with(entry['size'] + 1)

    def test_mirror_order_official_mode_and_raw_commit_identity(self):
        package = fonts.demo_fonts.FONT_SOURCES[0]
        path = fonts.urllib.parse.urlsplit(package['url']).path
        self.assertEqual(fonts.candidates(package),
                         ['https://mirrors.tuna.tsinghua.edu.cn' + path,
                          'https://mirrors.ustc.edu.cn' + path, package['url']])
        self.assertEqual(fonts.candidates(package, 'official'), [package['url']])
        raw = next(s for s in fonts.SOURCES if s['filename'] == 'InterVariable.ttf')
        self.assertEqual(fonts.candidates(raw, 'cn'), [raw['url']])
        self.assertEqual(fonts.candidates(raw, 'official'), [raw['url']])
        self.assertIn(fonts.INTER_COMMIT, raw['url'])
        with self.assertRaises(fonts.FontError):
            fonts.candidates(raw, 'untrusted-provider')
        self.assertEqual(fonts.SOURCES[0]['url'], package['url'])

    def test_shared_cache_receives_exact_package_pin_and_reuses_bounded_extract(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            payload = b'package fixture'
            package = base / 'package.deb'
            package.write_bytes(payload)
            member = b'font member fixture'
            notice = b'notice fixture'
            first = dict(source('Example.ttf', member), url='https://deb.debian.org/debian/pool/main/f/font/font_1_all.deb',
                         member='./font.ttf', package_size=len(payload), package_sha256=hashlib.sha256(payload).hexdigest())
            second = dict(source('COPYRIGHT.txt', notice, 'license'), url=first['url'], member='./copyright',
                          package_size=first['package_size'], package_sha256=first['package_sha256'])
            sources = (first, second)
            before = json.dumps(sources, sort_keys=True)
            with mock.patch.object(fonts.install_downloads, 'acquire', return_value=package) as fetch, \
                 mock.patch.object(fonts.demo_fonts, '_extract', return_value={'Example.ttf': member, 'COPYRIGHT.txt': notice}) as extract:
                result = fonts.acquire(base / 'fonts', sources, None, base / 'home',
                                       cache=base / 'cache/downloads', network='official')
            fetch.assert_called_once_with(base / 'cache/downloads', [first['url']], first['package_sha256'],
                                          size=first['package_size'], filename='font_1_all.deb')
            extract.assert_called_once_with(payload, list(sources))
            self.assertEqual(result, {'Example.ttf': member, 'COPYRIGHT.txt': notice})
            self.assertEqual(json.dumps(sources, sort_keys=True), before)
            self.assertFalse((base / 'fonts').exists())

    def test_integrity_error_stops_acquisition_without_new_font_fallback(self):
        entry = fonts.SOURCES[0]
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(fonts.install_downloads, 'acquire', side_effect=fonts.install_downloads.IntegrityError('SHA256 mismatch')) as fetch, \
             mock.patch.object(fonts.demo_fonts, '_extract') as extract:
            with self.assertRaisesRegex(fonts.FontError, 'SHA256 mismatch'):
                fonts.acquire(Path(temporary) / 'fonts', (entry,), None, Path(temporary) / 'home',
                              cache=Path(temporary) / 'cache/downloads')
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[1], fonts.candidates(entry))
        self.assertEqual(fetch.call_args.args[2], entry['package_sha256'])
        self.assertEqual(fetch.call_args.kwargs['size'], entry['package_size'])
        extract.assert_not_called()

    def test_raw_pins_use_safe_logical_filename_in_persistent_cache(self):
        entry = source('InterVariable.ttf', b'font')
        entry['url'] = fonts.INTER_BASE + 'Inter%5Bopsz,wght%5D.ttf'
        with tempfile.TemporaryDirectory() as temporary:
            cached = Path(temporary) / 'cached-font'
            cached.write_bytes(b'font')
            with mock.patch.object(fonts.install_downloads, 'acquire', return_value=cached) as fetch:
                self.assertEqual(fonts.download(entry, cache=Path(temporary) / 'cache', network='cn'), b'font')
            fetch.assert_called_once_with(Path(temporary) / 'cache', [entry['url']], entry['sha256'],
                                          size=entry['size'], filename='InterVariable.ttf')

    def test_windows_hkcu_and_notification_contract(self):
        registry = types.SimpleNamespace(HKEY_CURRENT_USER=1, KEY_QUERY_VALUE=2,
            KEY_SET_VALUE=4, REG_SZ=1, CreateKeyEx=mock.MagicMock(),
            QueryValueEx=mock.Mock(side_effect=FileNotFoundError), SetValueEx=mock.Mock())
        gdi, user = mock.Mock(), mock.Mock()
        gdi.AddFontResourceExW.return_value = 1
        user.SendMessageTimeoutW.return_value = 1
        with mock.patch.dict('sys.modules', {'winreg': registry}), mock.patch.object(
                fonts.ctypes, 'WinDLL', side_effect=[gdi, user], create=True):
            fonts.register_windows([Path('/fixture/Example.ttf')])
        self.assertEqual(registry.CreateKeyEx.call_args.args[0], registry.HKEY_CURRENT_USER)
        self.assertEqual(registry.SetValueEx.call_args.args[-1], '/fixture/Example.ttf')
        gdi.AddFontResourceExW.assert_called_once_with('/fixture/Example.ttf', 0, None)
        self.assertEqual(user.SendMessageTimeoutW.call_args.args[:2], (0xffff, 0x001d))
        registry.QueryValueEx.side_effect = None
        registry.QueryValueEx.return_value = ('other personal font', registry.REG_SZ)
        registry.SetValueEx.reset_mock()
        with mock.patch.dict('sys.modules', {'winreg': registry}), self.assertRaises(fonts.FontError):
            fonts.register_windows([Path('/fixture/Example.ttf')])
        registry.SetValueEx.assert_not_called()


if __name__ == '__main__':
    unittest.main()
