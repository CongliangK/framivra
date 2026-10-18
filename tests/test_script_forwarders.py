"""Compatibility scripts execute the owning source with unchanged argv/exit code."""
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


class ScriptForwarders(unittest.TestCase):
    def test_all_help_paths_match_owner_and_do_not_require_checkout_symlinks(self):
        bash = shutil.which('bash')
        if os.name == 'nt':
            git = shutil.which('git')
            candidate = Path(git).resolve().parent.parent / 'bin/bash.exe' if git else None
            if candidate and candidate.is_file(): bash = str(candidate)
        for name, destination in TARGETS.items():
            with self.subTest(entry=name):
                entry, target = ROOT / 'scripts' / name, ROOT / destination
                self.assertFalse(entry.is_symlink())
                self.assertTrue(target.is_file(), destination)
                prefix = [sys.executable] if entry.suffix == '.py' else [bash if entry.suffix == '.sh' else shutil.which('node')]
                self.assertTrue(prefix[0], 'required script interpreter missing')
                env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
                owner = subprocess.run(prefix + [target, '--help'], cwd=ROOT, env=env, capture_output=True, timeout=30)
                forwarded = subprocess.run(prefix + [entry, '--help'], cwd=ROOT, env=env, capture_output=True, timeout=30)
                self.assertEqual(forwarded.returncode, owner.returncode, forwarded.stderr.decode('utf-8','replace'))
                self.assertEqual(forwarded.stdout, owner.stdout)
                self.assertEqual(forwarded.stderr, owner.stderr)

    def test_import_exposes_owner_functions_with_owner_file(self):
        module = importlib.import_module('scripts.content_gates')
        owner_path = ROOT / 'apps/gates/content_gates.py'
        owner = runpy.run_path(str(owner_path), run_name='owner_fixture')
        self.assertEqual(Path(module.__file__).resolve(), owner_path.resolve())
        self.assertEqual(Path(module.fingerprint.__code__.co_filename).resolve(), owner_path.resolve())
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'input'; source.write_bytes(b'fingerprint input')
            self.assertEqual(module.fingerprint(source), owner['fingerprint'](source))
