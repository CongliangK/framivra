"""Portable-v2 migration anchors and fail-closed CLI behavior; stdlib/offline."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import demo, demo_fonts


class PortableEntryTests(unittest.TestCase):
    def test_new_revision_and_unchanged_legacy_evidence(self):
        baseline = demo.load_baseline()
        self.assertEqual(baseline['source']['runtime_sha256'],
                         '9b1c62621778de06cc0213f53ce037e4e697ae2fe2698bb761c1ddc1d0aa497e')
        historical = {
            'baseline.json': '684ba679949735d89529bce2922e49983768882e6738ea19b42dd747804891d3',
            'input-lock.json': 'bf06cbde6f461b9137875aae3131955a76aef1bfe1c273a076802041681cb9cf',
        }
        for name, expected in historical.items():
            self.assertEqual(demo.sha256_file(demo.PACKAGE / 'benchmark' / name), expected)
        lock = demo.load_input_lock(baseline)
        self.assertEqual(lock['source_runtime_sha256'], baseline['source']['runtime_sha256'])
        self.assertEqual(lock['provenance']['generator_sha256'],
                         demo.sha256_file(demo.PACKAGE / 'audio/build.py'))

    def test_wrong_digest_and_old_input_lock_are_rejected(self):
        baseline = demo.load_baseline()
        wrong = json.loads(json.dumps(baseline))
        wrong['source']['runtime_sha256'] = '0' * 64
        with self.assertRaisesRegex(demo.DemoError, 'digest differs'):
            demo.validate_manifest(wrong)
        legacy = demo.load_json_file_strict(demo.PACKAGE / 'benchmark/input-lock.json')
        with self.assertRaisesRegex(demo.DemoError, 'input lock identity'):
            demo.validate_input_lock(legacy, baseline)

    def test_legacy_receipt_points_to_fixed_original_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            (project / 'realization.json').write_text(json.dumps({
                'schema_version': 1, 'baseline_id': 'framivra-promo-timeline-svg-30s',
                'stages': [], 'complete': True}))
            with self.assertRaisesRegex(demo.DemoError, demo.LEGACY_CHECKOUT):
                demo.cmd_verify(argparse.Namespace(project=str(project)))

    def test_readonly_fonts_does_not_create_import_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            scripts = root / 'scripts'
            scripts.mkdir()
            for name in ('demo.py', 'demo_fonts.py'):
                shutil.copyfile(demo.ROOT / 'scripts' / name, scripts / name)
            before = {p.relative_to(root) for p in root.rglob('*')}
            # -E ignores an externally injected PYTHONDONTWRITEBYTECODE: the CLI
            # itself must prevent local bytecode writes, not rely on test setup.
            result = subprocess.run([sys.executable, '-E', str(scripts / 'demo.py'), 'fonts'],
                                    cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'MISSING')
            self.assertTrue(json.loads(result.stdout)['read_only'])
            self.assertEqual(before, {p.relative_to(root) for p in root.rglob('*')})

    def test_failed_public_acquisition_retains_failure_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve() / 'project'
            with patch.object(demo, 'dependency_identity', return_value={}), patch.object(
                    demo_fonts, 'fetch_fonts', side_effect=demo_fonts.FontError('offline fixture')), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = demo.main(['prepare', '--output', str(project), '--fetch-fonts'])
            self.assertEqual(code, 1)
            record = demo.load_json_file_strict(project / 'realization.json')
            self.assertFalse(record['complete'])
            self.assertIn('public font acquisition failed', record['error'])
            self.assertEqual(record['stages'], [])
            self.assertFalse((project / 'timeline.json').exists())

    def test_dependency_diagnostics_name_each_missing_tool(self):
        def probe(name):
            if name == 'node':
                raise demo.DemoError('Node.js 18+ required')
            return None if name in ('ffprobe', 'inkscape', 'fc-scan') else {'version_line': 'fixture'}
        with patch.object(demo, 'tool_identity', side_effect=probe), \
                patch.object(demo, 'package_version', return_value=None):
            with self.assertRaises(demo.DemoError) as failed:
                demo.dependency_identity(False)
        for name in ('node', 'ffprobe', 'inkscape', 'fc-scan', 'jsonschema'):
            self.assertIn(name + ':', str(failed.exception))
        self.assertIn('VTX_INKSCAPE', str(failed.exception))

    def test_inkscape_discovery_does_not_launch_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp).resolve() / '工具 # Inkscape'
            executable.write_bytes(b'discovery-only fixture, not a native renderer')
            with patch.dict(os.environ, {'VTX_INKSCAPE': str(executable)}), \
                    patch.object(demo.subprocess, 'run', side_effect=AssertionError('renderer launched')):
                identity = demo.tool_identity('inkscape')
            self.assertEqual(identity['executable'], str(executable))
            self.assertEqual(identity['binary_sha256'], demo.sha256_file(executable))
            self.assertEqual(identity['runtime_probe'], 'UNRUN')
            with patch.dict(os.environ, {'VTX_INKSCAPE': str(executable)}), \
                    patch.object(Path, 'resolve', side_effect=RuntimeError('Symlink loop')):
                with self.assertRaisesRegex(demo.DemoError, 'Inkscape path resolution failed'):
                    demo.tool_identity('inkscape')

    def test_old_node_and_invalid_version_encoding_fail(self):
        with patch.object(demo.shutil, 'which', return_value=sys.executable):
            for output in (b'v16.20.2\n', b'not a version\n', b'\xff\n'):
                with self.subTest(output=output), patch.object(demo.subprocess, 'run',
                        return_value=subprocess.CompletedProcess([], 0, output, b'')):
                    with self.assertRaises((demo.DemoError, UnicodeError)):
                        demo.tool_identity('node')

    def test_windows_reparse_components_are_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            candidate = base / 'new-project'
            original = Path.lstat
            def reparse(path, *args, **kwargs):
                if path == base:
                    return SimpleNamespace(st_mode=0o040755, st_file_attributes=0x400)
                return original(path, *args, **kwargs)
            with patch.object(Path, 'lstat', reparse):
                with self.assertRaisesRegex(demo.DemoError, 'reparse'):
                    demo.check_output_path(candidate)
                with self.assertRaisesRegex(demo.DemoError, 'symlink'):
                    demo.safe_relpath(base, 'asset.ttf')
            self.assertFalse(candidate.exists())

    def test_missing_fonts_explain_public_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(demo.DemoError, 'prepare --fetch-fonts'):
                demo.check_fonts(demo.source_files(demo.load_baseline()), Path(tmp).resolve())


if __name__ == '__main__':
    unittest.main()
