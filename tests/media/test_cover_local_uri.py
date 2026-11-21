"""The cover renderer passes a local filename, not a URL fragment, to Chromium."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit
from urllib.request import url2pathname

from apps.cover import make_cover


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
