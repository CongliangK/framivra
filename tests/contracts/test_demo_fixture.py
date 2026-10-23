"""Canonical 30-second Demo contracts and explicitly opted-in real generation.

The eight fast selectors retain path/strict-JSON/preflight and independent GFX
suite coverage. The five strict selectors require two REAL complete generations,
the external canonical audio anchor, product validate and byte-level negatives.
Canonical transfer is an additional route, never a substitute for generation.
Render/preview/QC/CEF/visual remain UNRUN here; no performance claim is made.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import scripts.demo as demo  # noqa: E402

# Preserve the strict inventory anchor and existing selector partition. Opt-in
# means missing canonical inputs/dependencies fail, never skip or accept-new.
DEMO_TOOLCHAIN = bool(os.environ.get('VTX_TEST_DEMO_TOOLCHAIN'))


def cli(*args, repo=REPO, env=None):
    return subprocess.run([sys.executable, 'scripts/demo.py', *map(str, args)],
                          capture_output=True, cwd=repo,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', **(env or {})})


class ContractTests(unittest.TestCase):
    """Read-only source contracts; no product imports or media generation."""

    def test_frozen_manifest_valid(self):
        baseline = demo.load_baseline()
        self.assertEqual(baseline['baseline_id'], demo.BENCHMARK_ID)
        self.assertEqual(baseline['source']['git_tree'], 'd5c869e3b998bbf61f7a1969293546066275ed17')
        self.assertEqual(baseline['workload']['duration_frames'], 900)
        demo.validate_manifest(baseline)
        with patch.object(demo.subprocess, 'run', side_effect=AssertionError('source must not require Git')):
            files = demo.source_files(baseline)
        self.assertEqual(set(files), set(baseline['runtime_files_sha256']))
        self.assertEqual(len(files), 22)
        for failure in (None, 'missing', 'directory', 'symlink', 'parent-symlink', 'samestat'):
            with self.subTest(source=failure), tempfile.TemporaryDirectory(prefix='promo-source-') as tmp:
                package = Path(tmp).resolve()
                for name, (data, mode) in files.items():
                    target = package / name; target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    self.assertEqual(mode, 0o644)
                # These source neighbors are neither inputs nor executable code.
                for name in ('README.md', 'benchmark/baseline.json', 'gfx/extra.svg', 'extra.js'):
                    target = package / name; target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text('untrusted neighbor')
                target = package / 'audio/build.py'
                if failure == 'missing': target.unlink()
                elif failure == 'directory': target.unlink(); target.mkdir()
                elif failure == 'symlink': target.unlink(); target.symlink_to(demo.PACKAGE / 'audio/build.py')
                elif failure == 'parent-symlink':
                    (package / 'audio').rename(package / 'audio-real')
                    (package / 'audio').symlink_to(package / 'audio-real', target_is_directory=True)
                elif failure == 'samestat':
                    before = target.stat(); data = bytearray(target.read_bytes()); data[-1] ^= 1
                    target.write_bytes(data); os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
                with patch.object(demo, 'PACKAGE', package):
                    if failure:
                        with self.assertRaises(demo.DemoError): demo.source_files(baseline)
                    else:
                        self.assertEqual(demo.source_files(baseline), files)

    def test_manifest_rejects_unknown_benchmark_and_bad_hash(self):
        with self.assertRaises(demo.DemoError):
            demo.validate_manifest({'baseline_id': 'other', 'format': 'framivra-source-baseline/v1'})
        baseline = demo.load_baseline()
        for field, value in [('commit', 'zz'), ('git_tree', '0' * 39), ('path', '../escape')]:
            broken = json.loads(json.dumps(baseline))
            broken['source'][field] = value
            with self.assertRaises(demo.DemoError, msg=field):
                demo.validate_manifest(broken)
        for name in ('README.md', 'benchmark/other.json', '../escape', '/escape', 'C:\\outside', './renderer.js', 'audio//build.py', ''):
            broken = json.loads(json.dumps(baseline))
            digest = broken['runtime_files_sha256'].pop('.gitignore')
            broken['runtime_files_sha256'][name] = digest
            with self.assertRaises(demo.DemoError, msg=name): demo.validate_manifest(broken)
        for hashes in (None, {}, dict(list(baseline['runtime_files_sha256'].items())[1:]),
                       {**baseline['runtime_files_sha256'], 'renderer.js': 'z' * 64}):
            with self.assertRaises(demo.DemoError):
                demo.validate_manifest({**baseline, 'runtime_files_sha256': hashes})
        # Schema-only malformed locks cannot become canonical anchors.
        with self.assertRaises(demo.DemoError):
            demo.validate_input_lock({'format': 'framivra-generated-input-lock/v1'}, baseline)

    def test_strict_json_rejects_duplicate_keys_and_nonfinite(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}'):
            with self.assertRaises(demo.DemoError, msg=text):
                demo.load_json_strict(text)

    def test_prepare_output_path_boundaries(self):
        with tempfile.TemporaryDirectory(prefix='promo-contract-') as tmp:
            tmp = Path(tmp).resolve()
            existing = tmp / 'exists'; existing.mkdir()
            link = tmp / 'link'; link.symlink_to(tmp, target_is_directory=True)
            cache = tmp / 'cache'; cache.mkdir()
            with patch.dict(os.environ, {'XDG_CACHE_HOME': str(cache)}):
                for case in (existing, link / 'new', tmp / 'missing/new', REPO / 'evil-out',
                             Path.home() / 'promo-evil', cache / 'new', '  '):
                    with self.assertRaises(demo.DemoError, msg=str(case)):
                        demo.check_output_path(str(case))
                self.assertEqual(demo.check_output_path(str(tmp / 'new')), tmp / 'new')

    def test_verify_path_escape_rejected(self):
        with tempfile.TemporaryDirectory(prefix='promo-path-') as tmp:
            base = Path(tmp).resolve()
            (base / 'link.txt').symlink_to('/etc/hostname')
            for name in ('/etc/passwd', '../outside', 'link.txt', 'C:\\outside', '.'):
                with self.assertRaises(demo.DemoError, msg=name):
                    demo.safe_relpath(base, name)

    def test_preflight_readonly_and_split_deps(self):
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in demo.PACKAGE.rglob('*') if p.is_file()}
        result = cli('preflight')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['schema_version'], 1)
        self.assertTrue(report['read_only'])
        self.assertTrue(report['prepare_supported'])
        self.assertTrue(report['checks']['source']['ok'])
        self.assertIsInstance(report['engine']['identity_known'], bool)
        self.assertEqual(set(report['checks']), {'baseline', 'source', 'input_lock', 'dependencies', 'fonts'})
        self.assertEqual(report['prepare_ready'], all(row['ok'] for row in report['checks'].values()))
        self.assertFalse(report['render']['ready'])
        self.assertIsNone(report['render']['cef'])
        self.assertIn('CEF', report['render']['cef_reason'])
        self.assertEqual(set(report['unrun']), set(demo.UNRUN_FIELDS))
        after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in demo.PACKAGE.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        # Transfer consumes no synth runtime, but does not waive actual validator tools.
        with patch.object(demo, 'tool_identity', return_value={'version_line': 'test-only', 'binary_sha256': '0' * 64}), patch.object(demo, 'package_version', side_effect=lambda name: None if name == 'numpy' else 'test-only'):
            self.assertIsNone(demo.dependency_identity(True)['numpy_version'])
            with self.assertRaises(demo.DemoError):
                demo.dependency_identity(False)
        for response in (FileNotFoundError(), subprocess.CompletedProcess([], 128, b'', b'not a repository'),
                         subprocess.CompletedProcess([], 0, str(REPO.parent).encode() + b'\n', b'')):
            with patch.object(demo.subprocess, 'run', **({'side_effect': response} if isinstance(response, Exception) else {'return_value': response})):
                identity = demo.engine_identity()
                self.assertFalse(identity['identity_known'])
                self.assertTrue(identity['reason'])
                self.assertTrue(all(identity[key] is None for key in ('commit', 'tree', 'status')))

    def test_preflight_gfx_stress_is_reference_only(self):
        result = cli('preflight')
        self.assertEqual(result.returncode, 0, result.stderr)
        references = json.loads(result.stdout)['regressions']
        self.assertEqual(references, [{'path': 'tests/gfx_bench_suite/suite_manifest.json',
                                      'mode': 'reference-only', 'execution': 'UNRUN'}])

    def test_gfx_stress_references_frozen_suite_only(self):
        suite = REPO / 'tests/gfx_bench_suite/suite_manifest.json'
        self.assertEqual(demo.sha256_file(suite), '1ba25c00b29f0b76efc0a63dc22acb251e1d208d2fce890a1c105e393175e821')
        scenarios = demo.load_json_file_strict(suite)['scenarios']
        pages = {page['path'] for scenario in scenarios if scenario['id'] != 'S8' for page in scenario['pages']}
        self.assertEqual(len(pages), 19)
        # Full SHA256 for every actual page, then fixed canonical serialization;
        # this is not the suite's shorter self-reported sha256_16 fingerprint.
        hashes = {name: demo.sha256_file(REPO / name) for name in sorted(pages)}
        aggregate = json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode('utf-8')
        self.assertEqual(hashlib.sha256(aggregate).hexdigest(),
                         '404901d857b21dcc1ce2d23a4deb32a650cd5c6bd69290bb0494c7cc641f496b')


@unittest.skipUnless(DEMO_TOOLCHAIN, 'real generation requires explicit VTX_TEST_DEMO_TOOLCHAIN=1; run python3 scripts/ci_checks.py demo-toolchain with the canonical input lock and existing dependencies')
class RealGenerationTests(unittest.TestCase):
    """Opt-in is a hard requirement: missing inputs/unsupported output never skip."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='promo-real-')).resolve()
        cls.projects = [cls.tmp / 'generated-1', cls.tmp / 'generated-2']
        cls.extra = ['--fonts-dir', os.environ['VTX_TEST_DEMO_FONTS_DIR']] if os.environ.get('VTX_TEST_DEMO_FONTS_DIR') else []
        cls.lock = demo.load_input_lock(demo.load_baseline())
        for output in cls.projects:
            result = cli('prepare', '--output', output, *cls.extra)
            if result.returncode:
                raise AssertionError('canonical real generation failed (not skipped); evidence retained at %s: %s %s' % (cls.tmp, result.stdout, result.stderr))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_f1_validate_exit0_and_raw_logs_kept(self):
        for output in self.projects:
            realization = json.loads((output / 'realization.json').read_text())
            self.assertTrue(realization['complete'])
            self.assertTrue(realization['engine']['identity_known'])
            self.assertTrue(all(demo.valid_hash(realization['engine'][key], 40) for key in ('commit', 'tree')))
            self.assertEqual(realization['excluded_paths'], ['README.md'])
            self.assertEqual(realization['runtime_source_count'], 22)
            self.assertFalse((output / 'README.md').exists())
            self.assertEqual(realization['audio_mode'], 'generated')
            self.assertEqual([s['stage'] for s in realization['stages']], list(demo.STAGES))
            self.assertTrue(all(s['exit_code'] == 0 for s in realization['stages']))
            for stage in ('generate-inputs', 'input-check', 'schema', 'validate'):
                for stream in ('stdout', 'stderr'):
                    self.assertTrue((output / 'logs' / f'{stage}.{stream}').is_file())
            self.assertEqual(json.loads((output / 'logs/validate.stdout').read_text())['exit_code'], 0)
            self.assertTrue(all(v == 'UNRUN' for v in realization['results'].values()))
            self.assertEqual(realization['canonical_input_lock_sha256'], demo.sha256_file(demo.INPUT_LOCK))

    def test_f2_same_toolchain_outputs_hash_equal(self):
        first, second = self.projects
        identities = [demo.load_json_file_strict(path / 'realization.json') for path in self.projects]
        for field in ('tools', 'numpy_version'):
            self.assertEqual(identities[0][field], identities[1][field])
        for name, expected in self.lock['audio'].items():
            digest = demo.sha256_file(first / name)
            self.assertEqual(digest, demo.sha256_file(second / name))
            self.assertEqual(digest, expected['sha256'])
            self.assertEqual(demo.audio_spec(first / name), expected)
            self.assertEqual(demo.audio_spec(second / name), expected)
        font_names = [row['path'] for row in demo.load_json_file_strict(first / 'fonts.lock.json')['fonts']]
        for name in ['gfx/demo.frames.json', *font_names]:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        for frame in range(900):
            name = f'gfx/frames-svg/f{frame:04d}.svg'
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        # Input verification also recomputes ALL SVG bytes from fixed source;
        # two matching projects are deliberately insufficient on their own.

    def test_f2_verify_decoded_specs_against_frozen_anchor(self):
        for output in self.projects:
            result = cli('verify', '--project', output)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['decoded_checks']['status'], 'checked')
            self.assertEqual(set(report['decoded_checks']['media']), set(demo.AUDIO_SAMPLES))
            for name, check in report['decoded_checks']['media'].items():
                self.assertEqual(check['mismatched_fields'], [])
                self.assertEqual(check['observed'], self.lock['audio'][name])
        # A canonical transfer is separately labelled, but uses the same verifier.
        with tempfile.TemporaryDirectory(prefix='promo-transfer-') as tmp:
            pack = Path(tmp).resolve() / 'audio'; pack.mkdir()
            for name in demo.AUDIO_SAMPLES:
                shutil.copyfile(self.projects[0] / name, pack / Path(name).name)
            output = Path(tmp).resolve() / 'transferred'
            result = cli('prepare', '--output', output, '--audio-inputs', pack, *self.extra)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertEqual(json.loads(result.stdout)['audio_mode'], 'canonical-transfer')
            result = cli('verify', '--project', output)
            self.assertEqual(result.returncode, 0, result.stderr)
            shutil.rmtree(output)  # One additional project at a time; no budget counters reset.
            # Real distribution layouts, not new native-engine performance evidence.
            base = Path(tmp).resolve(); upstream = base / 'upstream'; upstream.mkdir()
            tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=REPO).decode().split('\0')
            for name in filter(None, tracked):
                target = upstream / name; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / name, target)
            archive = base / 'archive'; shutil.copytree(upstream, archive)
            def git(*args, cwd=upstream):
                return subprocess.run(['git', '-c', 'user.name=Demo fixture', '-c', 'user.email=fixture@example.invalid',
                                       *map(str, args)], cwd=cwd, capture_output=True, check=True)
            git('init', '--quiet')
            git('commit', '--quiet', '--allow-empty', '-m', 'isolated distribution fixture root')
            git('add', '--force', '--all'); git('commit', '--quiet', '-m', 'candidate tracked source fixture')
            shallow = base / 'shallow'
            git('clone', '--quiet', '--depth', '1', upstream.as_uri(), shallow)
            self.assertEqual(git('rev-parse', '--is-shallow-repository', cwd=shallow).stdout.strip(), b'true')
            self.assertNotEqual(subprocess.run(['git', 'cat-file', '-e', demo.load_baseline()['source']['commit'] + '^{commit}'],
                                              cwd=shallow, capture_output=True).returncode, 0)
            archive = archive.rename(upstream / 'archive')
            self.assertFalse((archive / '.git').exists())
            self.assertEqual(Path(git('rev-parse', '--show-toplevel', cwd=archive).stdout.decode().strip()).resolve(), upstream)
            bin_dir = base / 'no-git-bin'; bin_dir.mkdir()
            for tool in ('node', 'ffmpeg', 'ffprobe', 'fc-scan', 'inkscape'):
                executable = shutil.which(tool)
                if tool == 'inkscape' and executable is None:
                    continue  # The production resolver may use VTX_INKSCAPE or the macOS app.
                self.assertIsNotNone(executable)
                (bin_dir / Path(executable).name).symlink_to(Path(executable).resolve())
            self.assertIsNone(shutil.which('git', path=str(bin_dir)))
            extra = ['--fonts-dir', str((REPO / Path(self.extra[1]).expanduser()).resolve())] if self.extra else []
            native_env = {'VTX_INKSCAPE': str((REPO / Path(os.environ['VTX_INKSCAPE'])).absolute())} if os.environ.get('VTX_INKSCAPE') else {}
            for kind, repo, env, known in (('shallow', shallow, {}, True), ('archive', archive, {}, False),
                                          ('no-git', archive, {'PATH': str(bin_dir)}, False)):
                with self.subTest(distribution=kind), tempfile.TemporaryDirectory(dir=base, prefix=kind + '-') as run:
                    env = {**native_env, **env}
                    result = cli('preflight', '--audio-inputs', pack, *extra, repo=repo, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertTrue(report['checks']['source']['ok'])
                    self.assertTrue(report['prepare_ready'], report)
                    self.assertEqual(report['engine']['identity_known'], known)
                    output = Path(run) / 'project'
                    result = cli('prepare', '--output', output, '--audio-inputs', pack, *extra, repo=repo, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                    record = json.loads((output / 'realization.json').read_text())
                    self.assertEqual(record['engine']['identity_known'], known)
                    if known:
                        self.assertEqual(record['engine']['commit'], git('rev-parse', 'HEAD', cwd=repo).stdout.decode().strip())
                    else:
                        self.assertEqual(record['engine']['reason'], 'Git executable unavailable' if kind == 'no-git' else 'Git root is not this source directory')
                        self.assertTrue(all(record['engine'][key] is None for key in ('commit', 'tree', 'status')))
                    result = cli('verify', '--project', output, repo=repo, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout)['engine'], record['engine'])

    def test_f3_verify_rejects_tampering(self):
        clean = self.projects[0]
        cases = ('svg', 'svg-invalid-utf8', 'svg-extra', 'font', 'version', 'generator', 'source-claim',
                 'source-bytes', 'source-readme-extra', 'receipt-root', 'receipt-stages', 'receipt-row', 'audio-encoded', 'audio-pcm', 'samestat', 'symlink')
        for name in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix='promo-tamper-') as tmp:
                case = shutil.copytree(clean, Path(tmp).resolve() / 'project', ignore=shutil.ignore_patterns('.generated', 'cache', 'out'))
                if name == 'svg':
                    target = case / 'gfx/frames-svg/f0899.svg'; target.write_bytes(target.read_bytes() + b'\n')
                elif name == 'svg-invalid-utf8':
                    target = case / 'gfx/frames-svg/f0899.svg'; target.write_bytes(target.read_bytes() + b'\xff')
                elif name == 'svg-extra':
                    (case / 'gfx/frames-svg/unexpected.svg').write_text('<svg/>')
                elif name == 'font':
                    target = next((case / 'assets/fonts').iterdir()); target.write_bytes(b'not a font')
                elif name in ('version', 'generator', 'source-claim'):
                    path = case / 'realization.json'; data = json.loads(path.read_text())
                    if name == 'version': data['schema_version'] = 99
                    elif name == 'generator': data['generator']['sha256'] = '0' * 64
                    else: data['source']['commit'] = '0' * 40
                    path.write_text(json.dumps(data))
                elif name.startswith('receipt-'):
                    path = case / 'realization.json'; data = json.loads(path.read_text())
                    if name == 'receipt-root': data = []
                    elif name == 'receipt-stages': data['stages'] = {}
                    else: data['stages'][0] = None
                    path.write_text(json.dumps(data))
                elif name == 'source-readme-extra':
                    (case / 'README.md').write_text('Not a runtime input.\n')
                elif name == 'source-bytes':
                    (case / 'audio/build.py').write_text('raise RuntimeError("must not execute")\n')
                elif name == 'symlink':
                    target = case / 'assets/audio/content-cue.wav'; target.unlink()
                    target.symlink_to(clean / 'assets/audio/content-cue.wav')
                else:
                    target = case / 'assets/audio/bed-premaster.wav'; previous = target.stat()
                    data = bytearray(target.read_bytes())
                    data[4 if name == 'audio-encoded' else 100] ^= 1
                    target.write_bytes(data)
                    if name == 'samestat': os.utime(target, ns=(previous.st_atime_ns, previous.st_mtime_ns))
                result = cli('verify', '--project', case)
                self.assertNotEqual(result.returncode, 0, name)
                if name.startswith('receipt-'):
                    diagnostic = json.loads(result.stderr)
                    self.assertFalse(diagnostic['ok'])
                    self.assertIn('error', diagnostic)
        control = cli('verify', '--project', clean)
        self.assertEqual(control.returncode, 0, control.stderr)

    def test_f4_prepare_negative_diagnostics_no_boundary_writes(self):
        with tempfile.TemporaryDirectory(prefix='promo-negative-') as tmp:
            base = Path(tmp).resolve()
            existing = base / 'exists'; existing.mkdir()
            link = base / 'link'; link.symlink_to(base, target_is_directory=True)
            before = sorted(str(p) for p in base.rglob('*'))
            for output in (existing, link / 'new', base / 'missing/new'):
                result = cli('prepare', '--output', output, *self.extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('error', result.stderr.decode('utf-8', 'replace'))
            self.assertEqual(before, sorted(str(p) for p in base.rglob('*')))
            for mode in ('encoded', 'pcm', 'samestat', 'missing', 'extra', 'symlink'):
                with self.subTest(transfer=mode), tempfile.TemporaryDirectory(dir=base) as case:
                    pack = Path(case).resolve() / 'audio'; pack.mkdir()
                    for name in demo.AUDIO_SAMPLES:
                        shutil.copyfile(self.projects[0] / name, pack / Path(name).name)
                    target = pack / 'content-cue.wav'
                    if mode == 'missing': target.unlink()
                    elif mode == 'extra': (pack / 'extra.wav').write_bytes(b'')
                    elif mode == 'symlink':
                        target.unlink(); target.symlink_to(self.projects[0] / 'assets/audio/content-cue.wav')
                    else:
                        previous = target.stat(); data = bytearray(target.read_bytes())
                        data[4 if mode == 'encoded' else 100] ^= 1
                        target.write_bytes(data)
                        if mode == 'samestat': os.utime(target, ns=(previous.st_atime_ns, previous.st_mtime_ns))
                    output = Path(case).resolve() / 'must-not-exist'
                    result = cli('prepare', '--output', output, '--audio-inputs', pack, *self.extra)
                    self.assertNotEqual(result.returncode, 0, mode)
                    self.assertIn('error', result.stderr.decode('utf-8', 'replace'))
                    self.assertFalse(output.exists(), mode)


if __name__ == '__main__':
    unittest.main()
