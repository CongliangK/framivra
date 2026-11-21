"""Offline cover browser selection and local filename transport."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit
from urllib.request import url2pathname

from apps.cover import make_cover
from vtx.render import browser_paths


class CoverWindowsBrowser(unittest.TestCase):
    def test_installed_edge_and_explicit_windows_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            edge = root / 'Edge install' / 'msedge.exe'
            edge.parent.mkdir()
            edge.touch()
            override = root / 'custom.exe'
            override.touch()
            (root / 'wrapper.cmd').touch()
            with mock.patch.object(make_cover.sys, 'platform', 'win32'), \
                    mock.patch.object(browser_paths, 'chrome_install_paths', return_value=(str(edge),)), \
                    mock.patch.dict(os.environ, {'VTX_CHROME': '', 'PATH': ''}):
                self.assertEqual(Path(make_cover.find_chrome()), edge)
                with mock.patch.dict(os.environ, {'VTX_CHROME': str(override)}):
                    self.assertEqual(Path(make_cover.find_chrome()), override)
                for name in ('missing.exe', 'wrapper.cmd'):
                    with self.subTest(name=name), mock.patch.dict(os.environ, {'VTX_CHROME': str(root / name)}):
                        with self.assertRaisesRegex(SystemExit, 'VTX_CHROME'):
                            make_cover.find_chrome()  # An invalid override cannot fall back to Edge.


class CoverLocalUri(unittest.TestCase):
    def test_render_opens_cover_html_inside_reserved_character_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / '项目 #100% 封面'
            output.mkdir()
            (output / 'bg_1920x1200.png').touch()
            launches = []

            def run(argv):
                if argv[0] == 'fixture-chromium':
                    launches.append(argv)
                else:
                    Path(argv[-1]).touch()  # cmd_render checks the encoder output size.

            with mock.patch.object(make_cover, 'find_chrome', return_value='fixture-chromium'), \
                    mock.patch.object(make_cover, 'run', side_effect=run), \
                    mock.patch.object(make_cover, 'probe', return_value=(1920, 1200)), \
                    mock.patch.object(sys, 'argv', ['make_cover.py', 'render', '--out', str(output),
                                                   '--title', 'Cover', '--sub', 'Local fixture']):
                make_cover.main()
            self.assertEqual(len(launches), 1)
            uri = urlsplit(launches[0][-1])
            self.assertEqual((uri.scheme, uri.netloc, uri.query, uri.fragment), ('file', '', '', ''))
            html = Path(url2pathname(uri.path))
            self.assertEqual(html, output / 'cover.html')
            self.assertIn('Local fixture', html.read_text(encoding='utf-8'))
