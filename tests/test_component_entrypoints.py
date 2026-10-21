"""Canonical component entry points work without legacy scripts or cwd coupling."""
import os
import importlib
import runpy
import tempfile
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
 'bed_static_duck.py': 'plugins/duck-static/bed_static_duck.py',
 'bilibili.py': 'apps/research/bilibili.py',
 'check_4k.sh': 'apps/publish/check_4k.sh',
 'compare_4k_quality.py': 'apps/publish/compare_4k_quality.py',
 'content_gates.py': 'apps/gates/content_gates.py',
 'denoise_init.sh': 'plugins/denoise/denoise_init.sh',
 'denoise_voice.py': 'plugins/denoise/denoise_voice.py',
 'dump-computed-styles.mjs': 'apps/research/dump-computed-styles.mjs',
 'export_4k.py': 'apps/publish/export_4k.py',
 'fetch-site-assets.py': 'apps/research/fetch-site-assets.py',
 'github_repos.py': 'apps/research/github_repos.py',
 'make_cover.py': 'apps/cover/make_cover.py',
 'repack_voice.py': 'apps/sync/repack_voice.py',
 'score_topic.py': 'apps/research/score_topic.py',
 'scroll-shoot.mjs': 'apps/research/scroll-shoot.mjs',
 'sync_audio.py': 'apps/sync/sync_audio.py',
}


class ComponentEntrypoints(unittest.TestCase):
    def test_canonical_help_is_cwd_independent(self):
        bash = shutil.which('bash')
        if os.name == 'nt':
            git = shutil.which('git')
            candidate = Path(git).resolve().parent.parent / 'bin/bash.exe' if git else None
            if candidate and candidate.is_file(): bash = str(candidate)
        for name, destination in TARGETS.items():
            with self.subTest(entry=name):
                target = ROOT / destination
                self.assertTrue(target.is_file(), destination)
                prefix = [sys.executable] if target.suffix == '.py' else [bash if target.suffix == '.sh' else shutil.which('node')]
                self.assertTrue(prefix[0], 'required script interpreter missing')
                env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
                owner = subprocess.run(prefix + [target, '--help'], cwd=ROOT, env=env, capture_output=True, timeout=30)
                # These two positional-only CLIs treat --help as a missing input;
                # retain their actual error contracts instead of accepting any failure.
                expected_errors = {'check_4k.sh': (2, b'FATAL: --help'),
                                   'score_topic.py': (1, b'--help/supplement.json')}
                expected_code, marker = expected_errors.get(name, (0, None))
                self.assertEqual(owner.returncode, expected_code, owner.stderr.decode('utf-8', 'replace'))
                self.assertNotIn(b'Traceback (most recent call last)', owner.stdout + owner.stderr)
                if marker:
                    self.assertIn(marker, owner.stdout + owner.stderr)

                with tempfile.TemporaryDirectory() as cwd:
                    independent = subprocess.run(prefix + [target, '--help'], cwd=cwd, env=env, capture_output=True, timeout=30)
                self.assertEqual(independent.returncode, owner.returncode, independent.stderr.decode('utf-8','replace'))
                self.assertEqual(independent.stdout, owner.stdout)
                self.assertEqual(independent.stderr, owner.stderr)

    def test_canonical_import_owns_fingerprint_implementation(self):
        module = importlib.import_module('apps.gates.content_gates')
        owner_path = ROOT / 'apps/gates/content_gates.py'
        owner = runpy.run_path(str(owner_path), run_name='owner_fixture')
        self.assertEqual(Path(module.__file__).resolve(), owner_path.resolve())
        self.assertEqual(Path(module.fingerprint.__code__.co_filename).resolve(), owner_path.resolve())
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input'; source.write_bytes(b'fingerprint input')
            self.assertEqual(module.fingerprint(source), owner['fingerprint'](source))
