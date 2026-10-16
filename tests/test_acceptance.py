"""Sealed-package unit/adversarial tests; fake renderer is only a unit-test seam.
The real CPU pipeline is exercised by scripts/acceptance_fixture.py.
"""
import copy
import ast
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vtx import acceptance as a


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.timeline = self.source / 'timeline.json'
        a.write_json(self.timeline, {'project': {}, 'sources': [{'id': 's', 'path': 'media.bin'}]})
        (self.source / 'media.bin').write_bytes(b'a' * 200000)
        self.package = self.root / 'package'
        probe = {'status': 'passed', 'argv': ['unit-probe'], 'exit_code': 0, 'stdout': 'unit version', 'stderr': ''}
        self.env = {'python': '3.12.0', 'framivra': '2.0.1', 'platform': 'test',
            'git': copy.deepcopy(probe), 'git_status': copy.deepcopy(probe),
            'kernel_files': {'vtx/test.py': {'sha256': '0' * 64, 'size': 0}},
            'runtime_backend_truth': {'status': 'unknown', 'reason': 'unit test'},
            'tools': {n: copy.deepcopy(probe) for n in ('ffmpeg', 'ffprobe')}}
        self.env['git'].update(argv=['git', '-C', str(a.REPO), 'rev-parse', 'HEAD'], stdout='a' * 40)
        self.env['git_status'].update(argv=['git', '-C', str(a.REPO), 'status', '--porcelain'], stdout='')
        for name in ('ffmpeg', 'ffprobe'):
            self.env['tools'][name]['argv'] = [name, '-version']

    def render(self, cmd, **kwargs):
        project = Path(cmd[4]).parent
        out = project / 'out'
        out.mkdir(exist_ok=True)
        if not (out / 'receipt.json').exists():
            a.write_json(out / 'receipt.json', {'schema': 'framivra.receipt.v2', 'timeline': a.file_digest(project / 'timeline.json'),
                'input_fingerprint': 'f' * 64,
                'source_identities': {'media.bin': {'schema': a.IDENTITY_SCHEMA, 'level': 'Verified',
                    'algorithm': 'sha256', 'scope': 'complete-file', 'digest': a.file_digest(project / 'media.bin')}},
                'stages': {n: {'ok': True, 'detail': 'unit test'} for n in a.STAGES}})
            a.write_json(out / 'qc_report.json', {'checks': [{'name': 'unit', 'pass': True}], 'skipped_probes': []})
            (out / 'final.mp4').write_bytes(b'unit test, not real video')
        return type('Result', (), {'returncode': 0})()

    def build(self, renderer=None):
        with patch.object(a, 'environment', return_value=self.env), \
             patch.object(a.subprocess, 'run', side_effect=renderer or self.render):
            self.result = a.build(self.timeline, self.package)
        return self.result

    def verify(self, source=True):
        return a.verify(self.package, self.result['manifest_sha256'], self.source if source else None)

    def reseal(self):
        # Models an authorized new anchor, to test semantic verification too.
        self.result['manifest_sha256'] = a.file_digest(self.package / 'manifest.json')

    def rewrite(self, name, value):
        (self.package / name).write_text(json.dumps(value))

    def test_realistic_package_and_visual_unknown(self):
        self.build()
        report = self.verify()
        self.assertTrue(report['integrity_verified'])
        self.assertFalse(report['accepted'])
        self.assertEqual(report['gates']['visual_content']['status'], 'unknown')
        self.assertFalse(report['dependency_closure']['self_contained'])

    def test_input_middle_mutation_with_restored_stat(self):
        self.build()
        p = self.source / 'media.bin'
        stat = p.stat()
        with p.open('r+b') as f:
            f.seek(100000)
            f.write(b'z')
        os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'live input changed'):
            self.verify()

    def test_artifact_and_snapshot_mutation(self):
        for name in ('artifacts/final.mp4', 'inputs/media.bin'):
            with self.subTest(name=name):
                if not self.package.exists():
                    self.build()
                p = self.package / name
                original = p.read_bytes()
                p.write_bytes(original + b'x')
                with self.assertRaises(ValueError):
                    self.verify()
                p.write_bytes(original)

    def test_manifest_tampering(self):
        self.build()
        m = a.read_json(self.package / 'manifest.json')
        m['run_id'] = 'altered'
        self.rewrite('manifest.json', m)
        with self.assertRaisesRegex(ValueError, 'manifest digest mismatch'):
            self.verify()

    def test_missing_evidence(self):
        self.build()
        (self.package / 'artifacts/qc_report.json').unlink()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            self.verify()

    def test_unknown_tools_and_absent_qc_are_not_passes(self):
        self.env['tools']['ffprobe'] = {'status': 'unknown', 'argv': ['ffprobe', '-version'], 'error': 'unit unavailable'}
        def render(cmd, **kwargs):
            self.render(cmd, **kwargs)
            (Path(cmd[4]).parent / 'out/qc_report.json').unlink(missing_ok=True)
            return type('R', (), {'returncode': 0})()
        self.build(render)
        report = self.verify()
        self.assertEqual(report['gates']['tool_versions']['status'], 'unknown')
        self.assertEqual(report['gates']['qc']['status'], 'unknown')

    def test_failed_run_retains_log_and_unknown_stages(self):
        def fail(cmd, **kwargs):
            kwargs['stdout'].write(b'unit failure\n')
            return type('R', (), {'returncode': 7})()
        self.build(fail)
        report = self.verify()
        self.assertEqual(report['gates']['process']['status'], 'failed')
        self.assertEqual(report['gates']['qc']['status'], 'unknown')
        self.assertIn(b'unit failure', (self.package / 'build.log').read_bytes())

    def test_change_during_build_is_recorded_failure(self):
        def mutate(cmd, **kwargs):
            result = self.render(cmd, **kwargs)
            (self.source / 'media.bin').write_bytes(b'changed')
            return result
        self.build(mutate)
        report = self.verify(source=False)
        self.assertEqual(report['gates']['input_stability']['status'], 'failed')

    def test_symlinks_and_output_reuse(self):
        self.build()
        with self.assertRaises(FileExistsError):
            a.build(self.timeline, self.package)
        p = self.package / 'artifacts/final.mp4'
        p.unlink()
        p.symlink_to(self.source / 'media.bin')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.verify()

    def test_source_symlink_and_parent_symlink(self):
        p = self.source / 'media.bin'
        p.unlink()
        p.symlink_to(self.timeline)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            a.build(self.timeline, self.package)
        link = self.root / 'alias'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            a.safe_path(link / 'output')

    def test_traversal_absolute_and_duplicate_json(self):
        for name in ('../outside', '/absolute', 'a/../b', 'a//b', 'C:/file', 'a\\b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                a.relative(name)
        self.timeline.write_text('{"project":{},"project":{}}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            a.collect_inputs(self.timeline)

    def test_reanchored_gate_lie_fails_semantic_check(self):
        self.build()
        m = a.read_json(self.package / 'manifest.json')
        m['gates']['visual_content']['status'] = 'passed'
        self.rewrite('manifest.json', m)
        self.reseal()
        with self.assertRaisesRegex(ValueError, 'gate report'):
            self.verify()

    def test_reanchored_duplicate_input_fails(self):
        self.build()
        m = a.read_json(self.package / 'manifest.json')
        m['inputs'].append(copy.deepcopy(m['inputs'][0]))
        self.rewrite('manifest.json', m)
        self.reseal()
        with self.assertRaisesRegex(ValueError, 'input inventory'):
            self.verify()

    def test_unknown_manifest_field_is_rejected(self):
        self.build()
        m = a.read_json(self.package / 'manifest.json')
        m['unexpected'] = True
        self.rewrite('manifest.json', m)
        self.reseal()
        with self.assertRaisesRegex(ValueError, 'unknown manifest'):
            self.verify()

    def test_source_also_declared_is_verified(self):
        tl = a.read_json(self.timeline)
        tl['project']['cache_dependencies'] = ['media.bin']
        self.timeline.write_text(json.dumps(tl))
        self.build()
        m = a.read_json(self.package / 'manifest.json')
        row = next(x for x in m['inputs'] if x['source_path'] == 'media.bin')
        self.assertEqual(row['runtime_identity_level'], 'Verified')
        self.verify()

    def test_legacy_package_stays_fast(self):
        self.build()
        m = a.read_json(self.package / 'manifest.json')
        m['schema'] = a.LEGACY_SCHEMA
        m.pop('receipt_identity_scope')
        m['receipt_identity_level'] = 'Fast'
        for row in m['inputs']:
            if row['role'] == 'source':
                row['runtime_identity_level'] = 'Fast'
                row['runtime_identity'] = a.media_stat_digest(self.package / row['path'])
        self.rewrite('manifest.json', m)
        self.reseal()
        self.assertTrue(self.verify()['integrity_verified'])
        m['receipt_identity_level'] = 'Verified'
        self.rewrite('manifest.json', m)
        self.reseal()
        with self.assertRaisesRegex(ValueError, 'runtime identity claim'):
            self.verify()

    def test_unexpected_receipt_stage_is_preserved(self):
        rows = a.gates({'stages': {'extension_gate': {'ok': False}}}, {}, 0, True, self.env)
        self.assertEqual(rows['extension_gate']['status'], 'failed')

    def test_environment_is_isolated(self):
        def render(cmd, **kwargs):
            self.assertNotIn('VTX_VIDEO_SEGMENTS', kwargs['env'])
            self.assertNotIn('SECRET_TEST', kwargs['env'])
            self.assertEqual(kwargs['env']['VTX_NO_NVENC'], '1')
            return self.render(cmd, **kwargs)
        with patch.dict(os.environ, {'VTX_VIDEO_SEGMENTS': '999', 'SECRET_TEST': 'hidden'}):
            self.build(render)

    def test_nested_schema_and_execution_repros(self):
        mutations = {
            'empty executions': lambda m: m['build'].update(executions=[]),
            'wrong exit': lambda m: m['build']['executions'][0].update(exit_code=7),
            'unknown build': lambda m: m['build'].update(extra=True),
            'unknown input': lambda m: m['inputs'][0].update(extra=True),
            'overbroad identity scope': lambda m: m.update(receipt_identity_scope='all-content'),
            'closure pass': lambda m: m['dependency_closure'].update(status='passed'),
            'false Fast token': lambda m: next(r for r in m['inputs'] if r['role'] == 'source').update(runtime_identity='0:0:0000000000000000'),
        }
        self.build()
        original = a.read_json(self.package / 'manifest.json')
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                m = copy.deepcopy(original)
                mutate(m)
                self.rewrite('manifest.json', m)
                self.reseal()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_missing_core_versions(self):
        self.build()
        original = a.read_json(self.package / 'manifest.json')
        for name in ('python', 'framivra'):
            with self.subTest(name=name):
                env = copy.deepcopy(self.env)
                env.pop(name)
                self.rewrite('environment.json', env)
                m = copy.deepcopy(original)
                m['files']['environment.json'] = a.describe(self.package / 'environment.json')
                self.rewrite('manifest.json', m)
                self.reseal()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_reserved_receipt_stage_rejected(self):
        with self.assertRaisesRegex(ValueError, 'reserved'):
            a.gates({'stages': {'process': {'ok': False}}}, {}, 0, True, self.env)

    def test_probes_do_not_inherit_ffreport(self):
        with patch.dict(os.environ, {'FFREPORT': 'file=outside.log', 'SECRET_TEST': 'secret'}):
            with patch.object(a.subprocess, 'run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = 'unit version'
                run.return_value.stderr = ''
                a.capture(['ffmpeg', '-version'])
                self.assertNotIn('FFREPORT', run.call_args.kwargs['env'])
                self.assertNotIn('SECRET_TEST', run.call_args.kwargs['env'])

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
    def test_actual_ffreport_does_not_write_outside_package(self):
        outside = self.root / 'unrequested.log'
        with patch.dict(os.environ, {'FFREPORT': 'file=' + str(outside)}):
            report = a.capture(['ffmpeg', '-version'])
        self.assertEqual(report['status'], 'passed')
        self.assertFalse(outside.exists())

    def test_isolated_empty_plugin_config(self):
        def render(cmd, **kwargs):
            config = Path(kwargs['env']['VTX_CONFIG'])
            self.assertEqual(json.loads(config.read_text()), {})
            self.assertNotEqual(config.parent, a.REPO)
            return self.render(cmd, **kwargs)
        self.build(render)
        self.verify()

    def test_probe_record_full_matrix(self):
        self.build()
        original = a.read_json(self.package / 'manifest.json')
        valid = copy.deepcopy(self.env)
        changes = [
            {'argv': [None]}, {'argv': ['echo', 'unrelated']},
            {'status': 'unknown'}, {'exit_code': False}, {'exit_code': []},
            {'stdout': {}}, {'stderr': False}, {'error': 'failed'},
            {'error': 123}, {'extra': True},
        ]
        for slot in ('git', 'git_status', 'ffmpeg', 'ffprobe'):
            for change in changes:
                with self.subTest(slot=slot, change=change):
                    env = copy.deepcopy(valid)
                    probe = env[slot] if slot in ('git', 'git_status') else env['tools'][slot]
                    probe.update(change)
                    self.rewrite('environment.json', env)
                    m = copy.deepcopy(original)
                    m['files']['environment.json'] = a.describe(self.package / 'environment.json')
                    self.rewrite('manifest.json', m)
                    self.reseal()
                    with self.assertRaises(ValueError):
                        self.verify()
        for slot in ('git', 'git_status', 'ffmpeg', 'ffprobe'):
            env = copy.deepcopy(valid)
            parent = env if slot in ('git', 'git_status') else env['tools']
            parent[slot] = {'status': 'unknown', 'argv': parent[slot]['argv']}
            self.rewrite('environment.json', env)
            m = copy.deepcopy(original)
            m['files']['environment.json'] = a.describe(self.package / 'environment.json')
            self.rewrite('manifest.json', m)
            self.reseal()
            with self.assertRaises(ValueError):
                self.verify()

    def test_valid_unknown_probe_variants(self):
        argv = ['ffmpeg', '-version']
        for probe in (
            {'status': 'unknown', 'argv': argv, 'error': 'not installed'},
            {'status': 'unknown', 'argv': argv, 'exit_code': 1, 'stdout': '', 'stderr': 'failure'},
            {'status': 'unknown', 'argv': argv, 'exit_code': 0, 'stdout': '', 'stderr': ''},
        ):
            a.validate_probe(probe, argv)

    def test_command_timeline_full_matrix(self):
        self.build()
        original = a.read_json(self.package / 'manifest.json')
        for path in ('/tmp/unrelated/wrong.json', '$PROJECT/wrong.json', '../timeline.json', None):
            with self.subTest(path=path):
                m = copy.deepcopy(original)
                for command in m['build']['commands']:
                    command[4] = path
                for i, row in enumerate(m['build']['executions']):
                    row['argv'] = m['build']['commands'][i]
                self.rewrite('manifest.json', m)
                self.reseal()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_receipt_fingerprint_full_matrix(self):
        self.build()
        original = a.read_json(self.package / 'manifest.json')
        receipt = a.read_json(self.package / 'artifacts/receipt.json')
        for value in ([], True, None, 'not-a-fingerprint', '', 'F' * 64, '0' * 63):
            with self.subTest(value=value):
                m = copy.deepcopy(original)
                m['receipt_input_fingerprint'] = value
                raw = copy.deepcopy(receipt)
                raw['input_fingerprint'] = value
                self.rewrite('artifacts/receipt.json', raw)
                m['files']['artifacts/receipt.json'] = a.describe(self.package / 'artifacts/receipt.json')
                self.rewrite('manifest.json', m)
                self.reseal()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_owned_metadata_type_matrix(self):
        self.build()
        original = a.read_json(self.package / 'manifest.json')
        for field, value in [('run_id', 'x'), ('created_at_utc', '2026-01-01T00:00:00'),
                ('created_at_utc', '2026-01-01T00:00:00+02:00'), ('accepted', 0),
                ('files', []), ('inputs', {}), ('gates', []), ('timeline', 'elsewhere/timeline.json')]:
            with self.subTest(field=field, value=value):
                m = copy.deepcopy(original)
                m[field] = value
                self.rewrite('manifest.json', m)
                self.reseal()
                with self.assertRaises((ValueError, TypeError, KeyError)):
                    self.verify()

    def test_every_fixture_probe_render_subprocess_has_explicit_env(self):
        for path in (Path(a.__file__), a.REPO / 'scripts/acceptance_fixture.py'):
            tree = ast.parse(path.read_text())
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr == 'run'
                     and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess']
            self.assertTrue(calls)
            for call in calls:
                self.assertIn('env', {kw.arg for kw in call.keywords})

    def test_skipped_qc_preserved(self):
        receipt = {'stages': {'qc': {'ok': True}}}
        rows = a.gates(receipt, {'checks': [{'pass': True}], 'skipped_probes': ['unit']}, 0, True, self.env)
        self.assertEqual(rows['qc']['status'], 'skipped')


if __name__ == '__main__':
    unittest.main()
