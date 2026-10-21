"""Explicit input identity stub for unit tests that fake the renderer itself.

Never used by real-browser acceptance; this does not constitute environment
evidence. Keeping this opt-in preserves the unknown/fail-closed production path.
"""
from pathlib import Path
import os
from unittest import mock
from vtx import gfx_environment as env


def collect(backend, binary, mode=None):
    path = Path(binary)
    return {'schema': env.SCHEMA, 'status': 'input-bound', 'backend': backend,
            'mode': mode, 'unit_fixture': True,
            'binary': env.digest(path.read_bytes().hex()) if path.is_file() else str(path)}


def find_chrome():
    return os.environ.get('VTX_CHROME') or 'unit-fixture-chrome'


def bind_environment(test):
    patch = mock.patch.object(env, 'collect', side_effect=collect)
    patch.start()
    test.addCleanup(patch.stop)
    browser = mock.patch('vtx.encoders.find_chrome', side_effect=find_chrome)
    browser.start()
    test.addCleanup(browser.stop)


def environment_cli():
    import sys
    return [sys.executable, '-c',
            "import runpy\nfrom unittest.mock import patch\n"
            "from tests.gfx._environment import collect, find_chrome\n"
            "with patch('vtx.gfx_environment.collect', side_effect=collect), "
            "patch('vtx.encoders.find_chrome', side_effect=find_chrome):\n"
            "    runpy.run_module('vtx', run_name='__main__')\n"]
