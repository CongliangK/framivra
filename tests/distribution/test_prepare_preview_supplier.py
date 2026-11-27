import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import install_source
from scripts.release import prepare_preview_supplier as supplier


@unittest.skipUnless(sys.platform == 'linux', 'Linux ordinary preview supplier')
class PreparePreviewSupplierTest(unittest.TestCase):
    def test_candidate_statement_never_authorizes_bytes(self):
        # Fixed test-only digests; neither is derived from arbitrary input at enrollment.
        launcher_pin = '9425addd014a17ef4302f973f0e83f302b0cc6498061a10b7a692cd26a9f90e0'
        wheel_pin = 'bf03283dd06df5bc98544d3325b3adabf7ba4010f39a3740a862ea562222e548'
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp).resolve()
            (candidate / 'vtx-preview').write_bytes(b'not an approved launcher')
            (candidate / 'kernel.whl').write_bytes(b'not an approved wheel')
            (candidate / 'candidate.json').write_text(json.dumps({
                'launcherSha256': supplier._sha((candidate / 'vtx-preview').read_bytes()),
                'wheelSha256': supplier._sha((candidate / 'kernel.whl').read_bytes()),
            }))
            with patch.object(supplier.os, 'geteuid', return_value=1000), patch.object(supplier, '_run') as run:
                with self.assertRaisesRegex(supplier.SupplierError, 'independently approved'):
                    supplier.qualify_candidate(candidate, wheel_filename='framivra-2.3.0-py3-none-any.whl',
                        launcher_sha256=launcher_pin, wheel_sha256=wheel_pin, work_parent=candidate, python=sys.executable)
                run.assert_not_called()

    def test_enroll_publishes_same_probe_directory_and_recovers_exact_match(self):
        approved = {'launcher_sha256': supplier._sha(b'fixture launcher'),
                    'wheel_sha256': supplier._sha(b'fixture wheel'), 'projection_sha256': 'a' * 64,
                    'wheel_filename': 'framivra-2.3.0-py3-none-any.whl', 'python': sys.executable}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            old = root / 'installation.json'
            old.write_bytes(b'{"status":"ready"}\n')
            launch, wheel = root / 'test-launch', root / 'test-wheel'
            launch.write_bytes(b'fixture launcher')
            wheel.write_bytes(b'fixture wheel')
            seen = []

            def probe(scratch, profile, *, fresh, expected_projection, **pins):
                self.assertEqual(expected_projection, 'a' * 64)
                seen.append(fresh is not None)
                if fresh is not None:
                    profile.mkdir(mode=0o700)
                    supplier._write(profile / 'vtx-preview', fresh[0], 0o500)
                    supplier._write(profile / 'kernel.whl', fresh[1])
                    supplier._write(profile / 'kernel.sha256', (pins['wheel_sha256'] + '\n').encode())
                    (profile / 'runtime').mkdir(mode=0o700)
                    self.assertFalse((root / 'preview').exists())
                    seen.append(profile.stat().st_ino)
                supplier._verified_profile(profile, pins['launcher_sha256'], pins['wheel_sha256'])
                return {'runtimeProjectionSha256': expected_projection}

            with patch.object(supplier, '_supervise', side_effect=lambda op, check: op(check)), \
                    patch.object(supplier, '_qualify_profile', side_effect=probe) as verify:
                result = supplier.enroll_profile(root, launcher_path=launch, prepare_wheel=lambda _scratch, _check: wheel.read_bytes(), **approved)
                self.assertEqual((root / 'preview').stat().st_ino, seen[1])
                self.assertEqual(old.read_bytes(), b'{"status":"ready"}\n')  # interruption before receipt
                again = supplier.enroll_profile(root, launcher_path=launch,
                    prepare_wheel=lambda _scratch, _check: self.fail('adoption must not rebuild'), **approved)
                self.assertEqual(again, result)
                self.assertEqual(seen[::2], [True, False])
                self.assertEqual(verify.call_count, 2)

    def test_mismatch_and_failed_probe_never_change_active_bytes(self):
        approved = {'launcher_sha256': supplier._sha(b'fixture launcher'),
                    'wheel_sha256': supplier._sha(b'fixture wheel'), 'projection_sha256': 'a' * 64,
                    'wheel_filename': 'framivra-2.3.0-py3-none-any.whl', 'python': sys.executable}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            launcher, wheel, profile = root / 'test-launch', root / 'test-wheel', root / 'preview'
            launcher.write_bytes(b'fixture launcher')
            wheel.write_bytes(b'fixture wheel')
            active = root / 'installation.json'
            active.write_bytes(b'{"status":"ready"}\n')
            with patch.object(supplier, '_qualify_profile', side_effect=supplier.SupplierError('fixture failed')):
                with self.assertRaisesRegex(supplier.SupplierError, 'fixture failed'):
                    supplier.enroll_profile(root, launcher_path=launcher, prepare_wheel=lambda _scratch, _check: wheel.read_bytes(), **approved)
            self.assertFalse(profile.exists())
            profile.mkdir(mode=0o700)
            foreign = profile / 'owner-data'
            foreign.write_bytes(b'unrelated')
            with self.assertRaisesRegex(supplier.SupplierError, 'unexpected entries'):
                supplier.enroll_profile(root, launcher_path=launcher,
                    prepare_wheel=lambda _scratch, _check: self.fail('conflict must not rebuild'), **approved)
            self.assertEqual(foreign.read_bytes(), b'unrelated')
            self.assertEqual(active.read_bytes(), b'{"status":"ready"}\n')

    def test_empty_public_catalog_is_nonprobing(self):
        root = Path('/not-a-real-installation')
        with patch.object(install_source, '_PREVIEW_SUPPLIER_RELEASES', {}), \
                patch.object(install_source, 'git') as git, patch.object(install_source.downloads, 'acquire') as acquire:
            self.assertIsNone(install_source.optional_preview_supplier(root, root, '0' * 40, install_source.REPOSITORY,
                                                                     sys.executable))
            git.assert_not_called()
            acquire.assert_not_called()

    def test_same_wheel_tree_does_not_authorize_changed_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).resolve()
            helper = source / 'scripts/release/prepare_preview_supplier.py'
            helper.parent.mkdir(parents=True)
            helper.write_text("raise AssertionError('unapproved helper executed')\n")
            listing = 'same wheel sources at both commits'
            key = hashlib.sha256((listing + '\n').encode()).hexdigest()
            approved = {key: {'wheelFilename': 'framivra-2.3.0-py3-none-any.whl', 'wheelBytes': 13,
                              'buildTools': {k: '1.2.3' for k in ('python','pip','setuptools','wheel')},
                              'helperSha256': hashlib.sha256(b'# approved helper\n').hexdigest(),
                              'launcherSha256': 'a' * 64, 'wheelSha256': 'b' * 64,
                              'runtimeProjectionSha256': 'c' * 64}}
            with patch.object(install_source, '_PREVIEW_SUPPLIER_RELEASES', approved), \
                    patch.object(install_source, 'git', return_value=listing), \
                    patch.object(install_source.downloads, 'acquire') as acquire:
                for commit in ('1' * 40, '2' * 40):
                    with self.assertRaisesRegex(install_source.InstallError, 'release-approved bytes'):
                        install_source.optional_preview_supplier(source, source, commit, install_source.REPOSITORY,
                                                                sys.executable)
                acquire.assert_not_called()

    def test_matching_catalog_hands_off_only_the_offline_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp).resolve()
            helper = source / 'scripts/release/prepare_preview_supplier.py'
            helper.parent.mkdir(parents=True)
            helper.write_bytes(b'''def build_approved_wheel(python, scratch, source, commit, **pins):
    pins['check']()
    return {'sourceTreeSha256': pins['source_tree_sha256'], 'commit': commit,
            'scratch': scratch.name, 'wheelSha256': pins['wheel_sha256']}
def enroll_profile(root, *, prepare_wheel, **pins):
    return prepare_wheel(root / '.private-build', pins['check'])
''')
            tree = 'reviewed inputs'
            key = supplier._sha((tree + '\n').encode('ascii'))
            record = {'wheelFilename': 'framivra-2.3.0-py3-none-any.whl', 'wheelBytes': 13,
                      'buildTools': dict(python='3.12.0', pip='24.0', setuptools='75.0', wheel='0.44.0'),
                      'helperSha256': supplier._sha(helper.read_bytes()), 'launcherSha256': 'a' * 64,
                      'wheelSha256': 'b' * 64, 'runtimeProjectionSha256': 'c' * 64}
            with patch.object(install_source, '_PREVIEW_SUPPLIER_RELEASES', {key: record}), \
                    patch.object(install_source, 'git', return_value=tree), \
                    patch.object(install_source.downloads, 'acquire') as acquire:
                enrollment = install_source.optional_preview_supplier(source, source, '1' * 40,
                                                                        install_source.REPOSITORY, sys.executable)
                self.assertEqual(enrollment, {'sourceTreeSha256': key, 'commit': '1' * 40,
                                              'scratch': '.private-build', 'wheelSha256': 'b' * 64})
                acquire.assert_not_called()

    def test_variant_catalog_checks_every_row_before_loading_helper(self):
        listing = 'reviewed inputs'
        key = supplier._sha((listing + '\n').encode())
        one = dict(buildTools=dict(python='3.13.0', pip='25.0', setuptools='80.0', wheel='0.48.0'),
                   wheelFilename='framivra-2.3.0-py3-none-any.whl', wheelBytes=11,
                   launcherSha256='a' * 64, wheelSha256='b' * 64, runtimeProjectionSha256='c' * 64)
        two = dict(one, buildTools=dict(one['buildTools'], python='3.14.0'), wheelSha256='d' * 64)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for variants in ((), (one, dict(one, wheelSha256='e' * 64)), (one, dict(two, wheelBytes=True)),
                             (one, dict(two, runtimeProjectionSha256='missing')), (one, None)):
                with self.subTest(variants=variants), \
                        patch.object(install_source, '_PREVIEW_SUPPLIER_RELEASES',
                                     {key: {'helperSha256': 'f' * 64, 'variants': variants}}), \
                        patch.object(install_source, 'git', return_value=listing), \
                        patch.object(install_source, '_preview_helper_bytes') as helper:
                    with self.assertRaisesRegex(install_source.InstallError, 'release-reviewed preview build'):
                        install_source.optional_preview_supplier(root, root, '1' * 40, install_source.REPOSITORY,
                                                                 sys.executable)
                    helper.assert_not_called()
            raw = b'def enroll_approved_variants(root, **kw): return kw\n'
            with patch.object(install_source, '_PREVIEW_SUPPLIER_RELEASES',
                              {key: {'helperSha256': supplier._sha(raw), 'variants': (one, two)}}), \
                    patch.object(install_source, 'git', return_value=listing), \
                    patch.object(install_source, '_preview_helper_bytes', return_value=raw) as helper:
                result = install_source.optional_preview_supplier(root, root, '1' * 40, install_source.REPOSITORY,
                                                                    sys.executable)
                helper.assert_called_once_with(root/'scripts/release/prepare_preview_supplier.py', supplier._sha(raw))
                self.assertEqual(result['variants'], (one, two))
                self.assertEqual(result['source_tree_sha256'], key)
            with patch.object(install_source, '_PREVIEW_SUPPLIER_RELEASES',
                              {key: {'helperSha256': supplier._sha(b''), 'variants': (one, two)}}), \
                    patch.object(install_source, 'git', return_value=listing), \
                    patch.object(install_source, '_preview_helper_bytes', return_value=b''):
                with self.assertRaisesRegex(install_source.InstallError, 'does not support build variants'):
                    install_source.optional_preview_supplier(root, root, '1' * 40, install_source.REPOSITORY,
                                                             sys.executable)

    def test_variants_select_full_row_only_inside_supervision_and_never_replace(self):
        launch_bytes = b'reviewed launcher'
        one = dict(buildTools=dict(python='3.13.0', pip='25.0', setuptools='80.0', wheel='0.48.0'),
                   wheelFilename='framivra-2.3.0-py3-none-any.whl', wheelBytes=1,
                   launcherSha256=supplier._sha(launch_bytes), wheelSha256=supplier._sha(b'A'),
                   runtimeProjectionSha256='a' * 64)
        two = dict(one, buildTools=dict(one['buildTools'], python='3.14.0', pip='26.0'),
                   wheelBytes=2, wheelSha256=supplier._sha(b'BB'), runtimeProjectionSha256='b' * 64)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            launcher = root/'launcher'; launcher.write_bytes(launch_bytes)
            receipt = root/'installation.json'; core = b'{"status":"ready"}\n'; receipt.write_bytes(core)
            current = [dict(one['buildTools'], python='3.13')]
            actions = []

            def supervised(op, parent_check):
                parent_check()
                return op(child_check)

            def child_check():
                pass

            def probe(python, scratch, env, *, check):
                self.assertIs(check, child_check)
                self.assertEqual(env, supplier._env(scratch))
                self.assertEqual(Path(env['HOME']), scratch)
                return current[0]

            def build(python, scratch, source, commit, **pins):
                self.assertIs(pins['check'], child_check)
                self.assertEqual(pins['source_tree_sha256'], 'c' * 64)
                row = one if current[0] == one['buildTools'] else two
                self.assertEqual(pins['build_tools'], row['buildTools'])
                self.assertEqual(pins['wheel_bytes'], row['wheelBytes'])
                self.assertEqual(pins['wheel_sha256'], row['wheelSha256'])
                actions.append('build')
                return b'A' if row is one else b'BB'

            def qualify(scratch, profile, *, fresh, expected_projection, **pins):
                row = one if current[0] == one['buildTools'] else two
                self.assertEqual(expected_projection, row['runtimeProjectionSha256'])
                self.assertEqual(pins['wheel_sha256'], row['wheelSha256'])
                if fresh is not None:
                    profile.mkdir(mode=0o700)
                    supplier._write(profile/'vtx-preview', fresh[0], 0o500)
                    supplier._write(profile/'kernel.whl', fresh[1])
                    supplier._write(profile/'kernel.sha256', (pins['wheel_sha256'] + '\n').encode())
                    (profile/'runtime').mkdir(mode=0o700)
                supplier._verified_profile(profile, pins['launcher_sha256'], pins['wheel_sha256'])
                actions.append('qualify')
                return {'wheel': row['wheelSha256']}

            def enroll(where=root):
                return supplier.enroll_approved_variants(where, launcher_path=launcher, source=root, commit='d'*40,
                    source_tree_sha256='c'*64, variants=(one, two), python=sys.executable)

            with patch.object(supplier.os, 'geteuid', return_value=0), \
                    patch.object(supplier, '_supervise') as supervisor:
                with self.assertRaisesRegex(supplier.SupplierError, 'unprivileged Linux'):
                    enroll()
                supervisor.assert_not_called()
            with patch.object(supplier, '_supervise', side_effect=supervised), \
                    patch.object(supplier, '_build_tools', side_effect=probe), \
                    patch.object(supplier, 'build_approved_wheel', side_effect=build), \
                    patch.object(supplier, '_qualify_profile', side_effect=qualify), \
                    patch.dict(os.environ, {'PYTHON_VERSION': one['buildTools']['python']}):
                with self.assertRaisesRegex(supplier.SupplierError, 'no unique'):
                    enroll()
                self.assertEqual(actions, [])
                self.assertFalse((root/'preview').exists())
                current[0] = one['buildTools']
                self.assertEqual(enroll(), {'wheel': one['wheelSha256']})
                profile = root/'preview'; inode = profile.stat().st_ino
                self.assertEqual(enroll(), {'wheel': one['wheelSha256']})
                current[0] = two['buildTools']
                with self.assertRaisesRegex(supplier.SupplierError, 'conflicts'):
                    enroll()
                self.assertEqual(actions, ['build', 'qualify', 'qualify'])
                self.assertEqual(profile.stat().st_ino, inode)
                self.assertEqual((profile/'kernel.whl').read_bytes(), b'A')
                self.assertEqual(receipt.read_bytes(), core)
                other = root/'other'; other.mkdir(mode=0o700)
                self.assertEqual(enroll(other), {'wheel': two['wheelSha256']})
                self.assertEqual((other/'preview/kernel.whl').read_bytes(), b'BB')

    def test_approved_offline_build_checks_toolset_and_wheel_before_qualification(self):
        versions = dict(python='3.12.0', pip='24.0', setuptools='75.0', wheel='0.44.0')
        wheel = b'a wheel candidate'
        filename = 'framivra-2.3.0-py3-none-any.whl'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            params = dict(source_tree_sha256='b' * 64, build_tools=versions, wheel_filename=filename,
                          wheel_bytes=len(wheel), wheel_sha256=supplier._sha(wheel), check=lambda: None)
            with patch.object(supplier, '_source_archive', return_value={'vt.py': b'reviewed'}) as archive, \
                    patch.object(supplier, '_build_tools', return_value=dict(versions, pip='25.0')) as tools, \
                    patch.object(supplier, '_build', return_value=(filename, wheel)) as build:
                with self.assertRaisesRegex(supplier.SupplierError, 'tool versions differ'):
                    supplier.build_approved_wheel(sys.executable, root, root/'source', 'a'*40, **params)
                build.assert_not_called()
                tools.return_value = versions
                with self.assertRaisesRegex(supplier.SupplierError, 'wheel differs'):
                    supplier.build_approved_wheel(sys.executable, root, root/'source', 'a'*40,
                                                  **dict(params, wheel_sha256='c'*64))
                self.assertEqual(supplier.build_approved_wheel(sys.executable, root, root/'source', 'a'*40, **params), wheel)
                self.assertEqual(archive.call_args.kwargs['expected_tree'], params['source_tree_sha256'])
                self.assertIs(build.call_args.kwargs['check'], params['check'])

    def test_optional_receipt_write_failure_leaves_confirmed_core_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            core = {'status': 'ready', 'commit': 'a' * 40}
            install_source.atomic_json(root / 'installation.json', core)
            with patch.object(install_source, '_PREVIEW_SUPPLIER_RELEASES', {'fixture': None}), \
                    patch.object(install_source, 'optional_preview_supplier', return_value={'fixture': True}), \
                    patch.object(install_source, 'atomic_json', side_effect=PermissionError('fixture write failure')), \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(install_source._register_optional_preview(root, root, core['commit'],
                    install_source.REPOSITORY, sys.executable, core), 0)
            self.assertEqual(install_source.json_record(root / 'installation.json'), core)
            self.assertIn('Core Ready; optional ordinary preview supplier not registered', errors.getvalue())

    def test_term_settles_owned_qualifier_and_grandchild_before_lock_release(self):
        # The test controller sends TERM only to a session it just created.
        script = """
import os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1]); from scripts import install_source as install
from scripts.release import prepare_preview_supplier as supplier
root = Path(sys.argv[2]); core = {'status':'ready'}
install.atomic_json(root/'installation.json', core)
install._PREVIEW_SUPPLIER_RELEASES = {'fixture': True}
child = ('import os, signal, sys, time; '
         'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
         'open(sys.argv[1],\"w\").write(str(os.getpid())); time.sleep(60)')
worker = ('import subprocess, sys; p=subprocess.Popen(sys.argv[1:]); p.wait()')
def enroll(*args, check):
    def probe(cancel):
        supplier._run([sys.executable, '-IB', '-c', worker, sys.executable, '-IB', '-c', child,
                       str(root/'owned.pid')], cwd=root, env=supplier._env(root), check=cancel, timeout=20)
        return {'unreachable': True}
    return supplier._supervise(probe, check)
install.optional_preview_supplier = enroll
with install.installation_lock(root):
    status = install._register_optional_preview(root,root,'0'*40,install.REPOSITORY,sys.executable,core)
    assert status == 143 and not Path('/proc', (root/'owned.pid').read_text()).exists()
    assert install.json_record(root/'installation.json') == core and not (root/'preview').exists()
sys.exit(status)
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            # Use a bounded readiness watch in the dedicated test session. No sleeps in the transaction.
            with subprocess.Popen([sys.executable, '-IB', '-c', script, str(Path(__file__).resolve().parents[2]), str(root)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True) as proc:
                import time
                deadline = time.monotonic() + 5
                while not (root / 'owned.pid').exists() and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                try:
                    self.assertTrue((root / 'owned.pid').exists(), 'qualification never spawned its grandchild')
                finally:
                    if proc.poll() is None:
                        proc.send_signal(signal.SIGTERM)
                _, errors = proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 143, errors.decode())
                self.assertIn(b'optional ordinary preview supplier not registered', errors)
            self.assertFalse((Path('/proc') / (root / 'owned.pid').read_text()).exists())

    def test_real_output_limit_stops_child_before_accepting_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(supplier.SupplierError, 'output limit'):
                supplier._run([sys.executable, '-IB', '-c', 'import os; os.write(1,b"x"*65536)'],
                              cwd=root, env=supplier._env(root), limit=64, timeout=3)

    def test_rejects_wrong_clock_and_projection_on_genuine_envelope_shape(self):
        media = b'\0\0\0\x10ftypmp42\0\0\0\0'
        pins = {'wheel_sha256': '1' * 64, 'runtime_projection_sha256': '2' * 64}
        version = 'sha256:' + '3' * 64
        response = {'schema': supplier._WINDOW, 'ok': True, 'content_version': version,
                    'launcher': supplier._LAUNCHER, **pins, 'input_epoch': 'sha256:' + '4' * 64,
                    'start': 0, 'end': 30, 'fps': {'num': 30, 'den': 1}, 'width': 1920, 'height': 1080,
                    'mime': 'video/mp4', 'audio_sample_rate': 48000, 'audio_channels': 2,
                    'base64': base64.b64encode(media).decode('ascii'), 'byte_length': len(media),
                    'sha256': hashlib.sha256(media).hexdigest()}
        wire = lambda item: (json.dumps(item) + '\n').encode()
        self.assertEqual(supplier._receipt(wire(response), pins, version), media)
        for update in ({'runtime_projection_sha256': '5' * 64}, {'fps': {'num': 30, 'den': True}}, {'end': 29}):
            with self.subTest(update=update), self.assertRaisesRegex(supplier.SupplierError, 'clock'):
                supplier._receipt(wire(dict(response, **update)), pins, version)
