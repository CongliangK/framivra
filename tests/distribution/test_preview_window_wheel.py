import base64
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import preview_window_wheel as launch


_DIST = 'framivra-2.3.0.dist-info/'
_MEMBERS = {
    'vt.py': b'# CLI',
    'vtx/__init__.py': b'# init',
    'vtx/runtime/preview_window_isolation.py': b'# window',
    'vtx/pluginlib/builtin_registry.json': b'{}',
    _DIST + 'METADATA': b'Name: framivra\nVersion: 2.3.0\n',
    _DIST + 'WHEEL': b'Root-Is-Purelib: true\nTag: py3-none-any\n',
    _DIST + 'RECORD': b'archive record',
}


def archive(extra=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as wheel:
        for name, data in {**_MEMBERS, **(extra or {})}.items():
            wheel.writestr(name, data)
    return stream.getvalue()


@unittest.skipUnless(sys.platform == 'linux', 'Linux wheel profile')
class PreviewWheelTests(unittest.TestCase):
    def test_rejects_unpinned_wheel_before_parsing(self):
        raw = archive()
        with patch.object(launch.zipfile, 'ZipFile', side_effect=AssertionError('opened unapproved archive')):
            with self.assertRaisesRegex(launch.ProfileError, 'configuration pin'):
                launch._wheel(raw, '0' * 64)

    def test_projects_only_matching_installed_contents(self):
        raw = archive()
        installed, projection = launch._wheel(raw, launch._sha(raw))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            site, target = root / 'site-packages', root / 'runtime'
            for name, data in _MEMBERS.items():
                item = site / name
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_bytes(data)
            (site / (_DIST + 'RECORD')).write_bytes(b'pip rewrote it')
            (site / 'vtx' / '__pycache__').mkdir()
            (site / 'vtx' / '__pycache__' / 'generated.pyc').write_bytes(b'not a runtime member')
            (site / 'unrelated.pth').write_bytes(b'not a runtime member')
            member = site / 'vtx/runtime/preview_window_isolation.py'
            member.write_bytes(b'# other!')
            with self.assertRaisesRegex(launch.ProfileError, 'changed installation member'):
                launch._stage(site, target, installed, projection)
            self.assertFalse(target.exists())

            member.write_bytes(_MEMBERS['vtx/runtime/preview_window_isolation.py'])
            original_umask = os.umask(0o002)
            try:
                launch._stage(site, target, installed, projection)
                # Refusal on an existing projection cannot replace its files.
                with self.assertRaises(FileExistsError):
                    launch._stage(site, target, installed, projection)
            finally:
                restored_umask = os.umask(original_umask)
            self.assertEqual(restored_umask, 0o002)
            launch._tree(target, projection)
            self.assertEqual(stat.S_IMODE((target / 'vtx/runtime').stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((target / 'VERSION').stat().st_mode), 0o600)
            self.assertEqual({p.name for p in target.iterdir()}, {'vtx', 'VERSION'})
            self.assertFalse((target / 'vtx' / '__pycache__').exists())
            (target / 'vtx' / 'extra.txt').write_bytes(b'unexpected')
            with self.assertRaisesRegex(launch.ProfileError, 'unexpected'):
                launch._tree(target, projection)

    def test_preview_launch_rejects_unverified_projection_before_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp).resolve()
            script = profile / 'vtx-preview'
            script.touch()
            raw = archive()
            (profile / 'kernel.whl').write_bytes(raw)
            (profile / 'kernel.sha256').write_text(launch._sha(raw) + '\n', encoding='ascii')
            argv = [str(script), '-t', 'unused-host-capability-placeholder', 'capabilities']
            # Bypass only unit-test interpreter/ancestor setup.
            with patch.object(launch, '_system_python'), patch.object(launch, '_trusted', return_value=True), \
                    patch.object(launch, '__file__', str(script)), patch.object(sys, 'argv', argv), \
                    patch.object(sys, 'path', list(sys.path)), patch.dict(launch.os.environ, {}, clear=True), \
                    contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(launch.main(), 2)
                self.assertNotIn(str(profile / 'runtime'), sys.path)
            self.assertEqual(out.getvalue(), '')
            self.assertIn('preview wheel refused:', err.getvalue())

    def _managed_fixture(self, root, *, managed_fonts=False):
        profile = root / 'preview'
        ordinary = b'''FIXED_WHEEL_LAUNCHER = 'framivra.preview-window.fixed-wheel/v1'
INPUT_EPOCH_ABI = 'framivra.preview-window.inputs/v1'
def run_preview_window_command(args, qualification):
    import json
    print(json.dumps(dict(qualification, operation=args[3])))
    return 0
'''
        if managed_fonts:
            ordinary = ordinary.replace(b'args, qualification', b'args, qualification, managed_font_profile=None')
            ordinary = ordinary.replace(b'operation=args[3]', b'operation=args[3], font_profile=str(managed_font_profile)')
            ordinary += b"MANAGED_FONTS_ABI = 'framivra.preview-window.managed-fonts/v1'\nWARM_SVG_MEDIA_ABI = 'framivra.preview-window.warm-svg-media/v1'\n"
        profile.mkdir(mode=0o700)
        script = profile / 'vtx-preview'
        script.write_bytes(Path(launch.__file__).read_bytes())
        sentinel = root / 'imported-vtx'
        raw = archive({
            'vtx/__init__.py': f'from pathlib import Path\nPath({str(sentinel)!r}).write_text("imported")\n'.encode(),
            'vtx/cli/timeline_preview_window.py': ordinary,
            'vtx/runtime/stdio.py': b'def ensure_blocking_stdio(): pass\n',
            'vtx/timeline.py': b'''def timeline_capabilities():
    return {'capabilities': {'preview_window': {'schema': 'framivra.timeline.preview-window/v1'}}}
''',
        })
        pin = launch._sha(raw)
        _, runtime = launch._wheel(raw, pin)
        (profile / 'kernel.whl').write_bytes(raw)
        (profile / 'kernel.sha256').write_text(pin + '\n', encoding='ascii')
        for member, data in runtime.items():
            path = profile / 'runtime' / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return script, sentinel, pin, launch._identity(runtime)

    def _managed_subprocess(self, script, args):
        # Run the actual counterpart; bypass only root/interpreter and ancestor
        # permission preconditions of the isolated test's synthetic temp root.
        bootstrap = '''import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader('managed_preview_test', sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher._system_python = lambda: None
launcher._trusted = lambda info: True
sys.argv = sys.argv[1:]
raise SystemExit(launcher.main())
'''
        return subprocess.run([sys.executable, '-IBS', '-c', bootstrap, str(script), *args],
                              env={key: value for key, value in os.environ.items() if key != 'VTX_CONFIG'},
                              capture_output=True, text=True, timeout=5)

    def test_managed_refusals_precede_any_import_or_preview_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            script, sentinel, pin, projection = self._managed_fixture(root)
            valid = f'--managed-preview-pins-v1={pin}:{projection}'
            capabilities = ['-t', 'unused', 'capabilities']
            render = ['-t', str(root / 'workspace/timeline.json'), 'timeline', 'preview-window',
                      '--frame', '0', '--expected-content-version=sha256:' + 'a' * 64,
                      '--render', '--include-media', '--prepare-inputs']
            # The local pin, alternate wheel bytes and tree all match each
            # other. The independent Host expectation must still refuse them.
            cases = [
                ([f'--managed-preview-pins-v1={"0" * 64}:{projection}', *capabilities], 'wheel pin differs'),
                ([f'--managed-preview-pins-v1={pin}:{"0" * 64}', *render], 'runtime projection differs'),
                ([f'--managed-preview-pins-v1={pin}:{"0" * 64}', '--first-frame-capabilities'], 'runtime projection differs'),
                ([f'--managed-preview-pins-v1={pin}:{"0" * 64}', '--formal-archive-capabilities'], 'runtime projection differs'),
                ([valid, valid, *capabilities], 'once as the leading argument'),
                (['-t', valid, 'capabilities'], 'once as the leading argument'),
                (['--verify', valid], 'once as the leading argument'),
                ([*render, valid], 'once as the leading argument'),
                ([valid.replace('v1=', 'v2='), *capabilities], 'malformed'),
                ([valid.replace(pin, pin.upper()), '*'], 'malformed'),
                ([valid + '\n', '--verify'], 'malformed'),
                (['--managed-preview-pins-v1', '--verify'], 'malformed'),
                ([valid, '--stage-from', str(root / 'site-packages')], 'cannot authorize staging'),
                ([valid, '--verify', *capabilities], 'only the fixed isolated'),
            ]
            for args, reason in cases:
                with self.subTest(args=args):
                    refused = self._managed_subprocess(script, args)
                    self.assertEqual(refused.returncode, 2, refused.stderr)
                    self.assertEqual(refused.stdout, '')
                    self.assertIn(reason, refused.stderr)
                    self.assertFalse(sentinel.exists(), 'vtx imported before rejecting the identity')
            # Correct prefix but altered projection tree still cannot import.
            (script.parent / 'runtime/vtx/__init__.py').write_text('# unapproved file', encoding='ascii')
            refused = self._managed_subprocess(script, [valid, *capabilities])
            self.assertEqual((refused.returncode, refused.stdout), (2, ''))
            self.assertFalse(sentinel.exists())

    def test_managed_marker_is_limited_to_verified_verify_and_capability_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            script, sentinel, pin, projection = self._managed_fixture(root)
            prefix = f'--managed-preview-pins-v1={pin}:{projection}'
            expected = {'ok': True, 'wheel_sha256': pin, 'runtime_projection_sha256': projection}
            marker = 'framivra.preview-window.managed-pins/v1'
            for args, record in [(['--verify'], expected), ([prefix, '--verify'], {**expected, 'managed_identity': marker})]:
                proc = self._managed_subprocess(script, args)
                self.assertEqual((proc.returncode, proc.stderr), (0, ''))
                self.assertEqual(json.loads(proc.stdout), record)
                self.assertFalse(sentinel.exists(), '--verify must remain import-free')
            capabilities = ['-t', 'unused', 'capabilities']
            for args, extra in [(capabilities, {}), ([prefix, *capabilities], {'managed_identity': marker})]:
                proc = self._managed_subprocess(script, args)
                self.assertEqual((proc.returncode, proc.stderr), (0, ''))
                self.assertEqual(json.loads(proc.stdout)['capabilities']['preview_window'], {
                    'schema': 'framivra.timeline.preview-window/v1', 'launcher': launch._LAUNCHER,
                    'wheel_sha256': pin, 'runtime_projection_sha256': projection, 'input_epoch': True, **extra})
                self.assertTrue(sentinel.is_file(), 'fixture did not exercise the real import')
            render = ['-t', str(root / 'workspace/timeline.json'), 'timeline', 'preview-window',
                      '--frame', '0', '--expected-content-version=sha256:' + '0' * 64,
                      '--render', '--include-media', '--prepare-inputs']
            proc = self._managed_subprocess(script, [prefix, *render])
            self.assertEqual((proc.returncode, proc.stderr), (0, ''))
            self.assertEqual(json.loads(proc.stdout), {'launcher': launch._LAUNCHER, 'wheel_sha256': pin,
                'runtime_projection_sha256': projection, 'operation': 'preview-window'})

    def test_managed_fonts_are_only_forwarded_for_verified_ordinary_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            script, _, pin, projection = self._managed_fixture(root, managed_fonts=True)
            prefix = f'--managed-preview-pins-v1={pin}:{projection}'
            render = ['-t', str(root / 'workspace/timeline.json'), 'timeline', 'preview-window',
                      '--frame', '0', '--expected-content-version=sha256:' + '0' * 64,
                      '--render', '--include-media', '--prepare-inputs']
            for args, expected in [([prefix, *render], str(script.parent)), (render, 'None'),
                                   ([prefix, *render, '--read-warm-svg'], 'None')]:
                proc = self._managed_subprocess(script, args)
                self.assertEqual((proc.returncode, proc.stderr), (0, ''))
                self.assertEqual(json.loads(proc.stdout)['font_profile'], expected)

    @unittest.skipUnless(os.environ.get('FRAMIVRA_TEST_PRIVATE_PREVIEW_ROOT'),
                         'requires an explicitly provisioned private system-Python fixture root')
    def test_real_unprivileged_launcher_accepts_only_the_bound_managed_fixture(self):
        trusted_root = Path(os.environ['FRAMIVRA_TEST_PRIVATE_PREVIEW_ROOT'])
        with tempfile.TemporaryDirectory(dir=trusted_root) as tmp:
            root = Path(tmp)
            script, sentinel, wheel, runtime = self._managed_fixture(root)
            script.chmod(0o700)

            def invoke(*args):
                return subprocess.run([str(script), *args], env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
                                      capture_output=True, text=True, timeout=5, cwd=script.parent)

            pins = f'--managed-preview-pins-v1={wheel}:{runtime}'
            for invalid in (f'--managed-preview-pins-v1={"0" * 64}:{runtime}',
                            f'--managed-preview-pins-v1={wheel}:{"0" * 64}'):
                refused = invoke(invalid, '-t', 'unused', 'capabilities')
                self.assertEqual((refused.returncode, refused.stdout), (2, ''))
                self.assertFalse(sentinel.exists())
            verify = invoke(pins, '--verify')
            self.assertEqual((verify.returncode, verify.stderr), (0, ''))
            self.assertEqual(json.loads(verify.stdout), {'ok': True, 'wheel_sha256': wheel,
                'runtime_projection_sha256': runtime, 'managed_identity': launch._MANAGED_IDENTITY})
            self.assertFalse(sentinel.exists())
            legacy = invoke('-t', 'unused', 'capabilities')
            self.assertEqual((legacy.returncode, legacy.stderr), (0, ''))
            self.assertNotIn('managed_identity', json.loads(legacy.stdout)['capabilities']['preview_window'])
            managed = invoke(pins, '-t', 'unused', 'capabilities')
            self.assertEqual((managed.returncode, managed.stderr), (0, ''))
            cap = json.loads(managed.stdout)['capabilities']['preview_window']
            self.assertEqual((cap['managed_identity'], cap['wheel_sha256'], cap['runtime_projection_sha256']),
                             (launch._MANAGED_IDENTITY, wheel, runtime))
            self.assertTrue(sentinel.is_file())

    def test_formal_archive_probe_preserves_callers_nonblocking_stdout(self):
        repo = Path(launch.__file__).resolve().parents[1]
        # A verified older wheel still returns a real (disabled) archive probe.
        stdio = 'vtx/runtime/stdio.py'
        raw = archive({stdio: (repo / stdio).read_bytes()})
        pin = launch._sha(raw)
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp).resolve()
            script = profile / 'vtx-preview'
            script.touch()
            (profile / 'kernel.whl').write_bytes(raw)
            (profile / 'kernel.sha256').write_text(pin + '\n', encoding='ascii')
            _, runtime = launch._wheel(raw, pin)
            for member, data in runtime.items():
                path = profile / 'runtime' / member
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
            try:
                # Bypass only platform/ancestor constraints of the test's temp path.
                proc = subprocess.run([sys.executable, '-B', '-c',
                    'import sys; from scripts import preview_window_wheel as launch; '
                    'launch._system_python = lambda: None; launch._trusted = lambda info: True; '
                    'launch.__file__ = sys.argv[1]; sys.argv = sys.argv[1:]; '
                    'raise SystemExit(launch.main())', str(script), '--formal-archive-capabilities'],
                    cwd=repo, stdout=write_fd, stderr=subprocess.PIPE, timeout=10,
                    env={key: value for key, value in os.environ.items() if key != 'VTX_CONFIG'})
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertFalse(os.get_blocking(write_fd))
                probe = json.loads(os.read(read_fd, 4096))
                self.assertEqual(probe['schema'], 'framivra.preview-formal-archive.capabilities/v1')
                self.assertIs(probe['available'], False)
            finally:
                os.close(write_fd)
                os.close(read_fd)

    def test_requires_separate_project_and_fixed_preview_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            profile = root / 'host'
            timeline = str(root / 'session/timeline.json')
            window = ['timeline', 'preview-window', '--frame', '1', '--expected-content-version=sha256:' + 'a' * 64,
                      '--render', '--include-media', '--prepare-inputs']
            self.assertEqual(launch._operation(['-t', timeline, *window], profile), 'preview-window')
            warm = ['-t', timeline, *window[:-3], '--read-warm-svg']
            self.assertEqual(launch._operation(warm, profile), 'preview-warm-svg')
            warm_media = ['-t', timeline, *window, '--read-warm-svg']
            self.assertEqual(launch._operation(warm_media, profile), 'preview-warm-svg-media')
            program = ['-t', timeline, *window, '--use-program-audio']
            self.assertEqual(launch._operation(program, profile), 'preview-program-audio')
            self.assertEqual(launch._operation([*warm_media, '--use-program-audio'], profile),
                             'preview-warm-svg-program-audio')
            for args in ([*warm, '--render', '--prepare-inputs'], [*warm, '--use-program-audio'],
                         [*program, '--read-warm-svg'], [*program, '--use-program-audio'],
                         [*program[:-2], '--use-program-audio'],
                         [*warm_media[:-2], '--read-warm-svg'], [*warm, *window[-3:]],
                         [*warm_media, '--read-warm-svg']):
                with self.subTest(args=args), self.assertRaises(launch.ProfileError):
                    launch._operation(args, profile)
            self.assertEqual(launch._operation(['-t', 'placeholder', 'capabilities'], profile), 'capabilities')
            for args in (['-t', str(profile / 'inside.json'), *window],
                         ['-t', str(root / 'ancestor.json'), *window], ['-t', 'relative.json', *window],
                         *(['-t', timeline, *operation] for operation in (['inspect'], ['query'], ['reference'],
                           ['store', 'capabilities'], ['timeline', 'frame'], ['capabilities', '--render'])),
                         ['-t', timeline, *window[:-2], window[-1], window[-2]]):
                with self.subTest(args=args), self.assertRaises(launch.ProfileError):
                    launch._operation(args, profile)

    def _formal_fixture(self, root):
        profile, timeline = root / 'trusted', root / 'project/cut.json'
        profile.mkdir(mode=0o700)
        (timeline.parent / 'assets').mkdir(parents=True)
        # These are identity-only fixtures, not independently decoded media.
        sources = {'assets/video.mp4': b'video', 'assets/unused.wav': b'unused'}
        for path, data in sources.items():
            (timeline.parent / path).write_bytes(data)
        project = {'version': 2, 'project': {'fps': 30, 'width': 1920, 'height': 1080, 'duration_frames': 90},
                   'sources': [{'id': str(i), 'path': p} for i, p in enumerate(sources)],
                   'video_tracks': [{'name': 'video', 'kind': 'aroll', 'clips': [
                       {'src': '0', 'src_in': 0, 'src_out': 90, 'dst_in': 0, 'dur': 90}]}],
                   'audio_tracks': [], 'subtitles': []}
        timeline.write_bytes(json.dumps(project).encode('utf-8') + b'\n')
        proof = {'launcher': launch._LAUNCHER, 'wheel_sha256': 'a' * 64, 'runtime_projection_sha256': 'b' * 64}
        media = b'\x00\x00\x00\x18ftypisom0123456789ab'
        facts = lambda data: {'sha256': launch._sha(data), 'byte_length': len(data)}
        manifest = {'schema': 'framivra.preview-formal-archive.manifest/v1', 'scope': 'operator-approved-formal-av-archive',
                    'timeline': str(timeline), 'content_version': 'sha256:' + launch._sha(timeline.read_bytes()),
                    'start': 0, 'end': 60, 'fps': {'num': 30, 'den': 1}, 'sources': {p: facts(d) for p, d in sources.items()},
                    **facts(media), **{k: proof[k] for k in ('wheel_sha256', 'runtime_projection_sha256')}}
        raw = json.dumps(manifest).encode('utf-8')
        (profile / 'formal-archive.json').write_bytes(raw)
        (profile / 'formal-archive.mp4').write_bytes(media)
        (profile / 'formal-archive.sha256').write_text(launch._sha(raw) + '\n', encoding='ascii')
        args = ['-t', str(timeline), 'timeline', 'preview-formal-archive', '--frame', '0',
                '--expected-content-version=' + manifest['content_version'], '--read-approved-archive', '--nonce=' + 'e' * 32]
        return profile, timeline, proof, manifest, media, args

    def _formal_call(self, profile, proof, args, *, reverify=lambda: None, qualified=True, status=0):
        op = launch._operation(args, profile)
        with patch.object(launch, '_write_record') as write:
            runtime = {launch._FORMAL_ARCHIVE_MEMBER} if qualified else set()
            self.assertEqual(launch._dispatch_formal_archive(op, args, profile, proof, reverify, runtime), status)
        write.assert_called_once()
        data, _ = write.call_args.args
        self.assertLessEqual(len(data), 8 * 1024**2)
        self.assertEqual(data.count(b'\n'), 1)
        return json.loads(data)

    def test_formal_archive_uses_only_the_separately_pinned_exact_selector(self):
        from vtx.runtime import preview_formal_archive as formal

        with tempfile.TemporaryDirectory() as tmp:
            profile, timeline, proof, manifest, media, args = self._formal_fixture(Path(tmp).resolve())
            probe = ['--formal-archive-capabilities']
            expected = {'schema': 'framivra.preview-formal-archive.capabilities/v1', 'available': True, **proof}
            self.assertEqual(self._formal_call(profile, proof, probe), expected)
            self.assertEqual(self._formal_call(profile, proof, probe, qualified=False), dict(expected, available=False))
            result = self._formal_call(profile, proof, args)
            self.assertEqual(result, {'schema': 'framivra.preview-formal-archive/v1', 'ok': True, 'nonce': 'e' * 32,
                                      'width': 1920, 'height': 1080, 'audio_sample_rate': 48000, 'audio_channels': 2,
                                      'mime': 'video/mp4', 'base64': base64.b64encode(media).decode('ascii'),
                                      'manifest_sha256': launch._sha((profile / 'formal-archive.json').read_bytes()), **proof,
                                      **{k: manifest[k] for k in ('content_version', 'start', 'end', 'fps', 'byte_length', 'sha256')}})
            with patch.object(formal, 'pin_inputs', side_effect=AssertionError('opened an unapproved selector')):
                for wrong in ([args[0], str(timeline.parent / 'out/secret.json'), *args[2:]],
                              [*args[:5], '1', *args[6:]],
                              [*args[:6], '--expected-content-version=sha256:' + '1' * 64, *args[7:]]):
                    self.assertEqual(self._formal_call(profile, proof, wrong, status=1)['code'], 'archive.selector_mismatch')
            with self.assertRaises(launch.ProfileError):
                launch._operation([*args, '--archive=/anywhere'], profile)
            (profile / 'formal-archive.sha256').unlink()
            # An operator grant is deliberately irrelevant to this probe.
            (profile / 'operator-span.json').write_bytes(b'{}')
            self.assertEqual(self._formal_call(profile, proof, probe), dict(expected, available=False))
            self.assertEqual(self._formal_call(profile, proof, args, status=1)['code'], 'archive.unavailable')

    def test_formal_archive_rejects_changed_bytes_before_or_during_consumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile, timeline, proof, _, media, args = self._formal_fixture(Path(tmp).resolve())
            source = timeline.parent / 'assets/unused.wav'
            for path, broken, error in ((timeline, timeline.read_bytes()[:-1] + b' ', 'archive.inputs_mismatch'),
                                        (source, b'Unused', 'archive.inputs_mismatch'),
                                        (profile / 'formal-archive.mp4', media[:-1] + b'!', 'archive.unavailable')):
                with self.subTest(path=path):
                    original = path.read_bytes()
                    path.write_bytes(broken)
                    self.assertEqual(self._formal_call(profile, proof, args, status=1)['code'], error)
                    path.write_bytes(original)
            result = self._formal_call(profile, proof, args, status=1, reverify=lambda: source.write_bytes(b'Unused'))
            self.assertEqual(result['code'], 'archive.inputs_mismatch')

    def test_formal_archive_rejects_even_approved_nonplain_timeline_and_linked_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile, timeline, proof, manifest, _, args = self._formal_fixture(Path(tmp).resolve())
            source = timeline.parent / 'assets/unused.wav'
            alias = timeline.parent / 'ordinary.wav'
            source.rename(alias)
            source.symlink_to(alias)
            self.assertEqual(self._formal_call(profile, proof, args, status=1)['code'], 'archive.inputs_mismatch')
            source.unlink()
            alias.rename(source)
            doc = json.loads(timeline.read_bytes())
            for mutate in (lambda d: d.update(subtitles_src='out/subtitles.srt'),
                           lambda d: d['sources'][0].update(bgm={'output': 'master'}),
                           lambda d: d['video_tracks'][0].update(kind='gfx')):
                changed = json.loads(json.dumps(doc))
                mutate(changed)
                data = json.dumps(changed).encode('utf-8')
                timeline.write_bytes(data)
                manifest['content_version'] = 'sha256:' + launch._sha(data)
                args[6] = '--expected-content-version=' + manifest['content_version']
                raw = json.dumps(manifest).encode('utf-8')
                (profile / 'formal-archive.json').write_bytes(raw)
                (profile / 'formal-archive.sha256').write_text(launch._sha(raw) + '\n', encoding='ascii')
                self.assertEqual(self._formal_call(profile, proof, args, status=1)['code'], 'archive.inputs_mismatch')

    def test_stage_is_written_to_wire_not_redirected_render_log(self):
        r = subprocess.run([sys.executable, '-B', '-c',
            'import contextlib,sys; from scripts.preview_window_wheel import _write_record; '
            'ctx=contextlib.redirect_stdout(sys.stderr); ctx.__enter__(); '
            'print("diagnostic"); _write_record(b"frame",lambda:None)'],
            capture_output=True, check=True, cwd=Path(launch.__file__).resolve().parents[1])
        self.assertEqual((r.stdout, r.stderr), (b'frame', b'diagnostic\n'))

    def test_first_frame_probe_and_launcher_use_only_the_opt_in_fixed_abi(self):
        from vtx.cli import timeline_preview_window as cli
        from vtx.runtime import preview_window_stream as stream

        nonce = 'c' * 32
        args = ['-t', '/project/timeline.json', 'timeline', launch._FIRST_FRAME, '--frame', '1',
                '--expected-content-version=sha256:' + 'a' * 64,
                '--render', '--include-media', '--prepare-inputs', '--nonce=' + nonce]
        profile = Path('/trusted-profile')
        self.assertEqual(launch._operation(args, profile), launch._FIRST_FRAME)
        self.assertEqual(launch._operation([launch._FIRST_FRAME_PROBE], profile), launch._FIRST_FRAME_PROBE)
        for invalid in (args[:-1], args + ['--other'], [*args[:-1], '--nonce=' + 'C' * 32],
                        [*args[:3], 'preview-window', *args[4:]]):
            with self.assertRaises(launch.ProfileError):
                launch._operation(invalid, profile)
        self.assertFalse(cli.matches(args))

        proof = {'launcher': launch._LAUNCHER, 'wheel_sha256': 'a' * 64, 'runtime_projection_sha256': 'b' * 64}
        events = []

        def command(argv, *, qualification, nonce, reverify_profile, emit):
            self.assertEqual(argv, [*args[:3], 'preview-window', *args[4:10]])
            self.assertEqual(qualification, proof)
            self.assertEqual(nonce, 'c' * 32)
            reverify_profile()
            emit({'provisional': True}, lambda: None)
            events.append('settlement failed')
            return 1, {'ok': False}

        def write(data, check):
            check()
            events.append((data[:1], json.loads(data[5:])))

        with patch('vtx.timeline_preview_window.supported', return_value=True), \
                patch.object(cli, 'FIRST_FRAME_ABI', None), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(launch._dispatch_first_frame(launch._FIRST_FRAME_PROBE, [], proof, lambda: None), 0)
            self.assertFalse(json.loads(out.getvalue())['available'])
        with patch('vtx.timeline_preview_window.supported', return_value=True), \
                patch.object(cli, 'run_first_frame_command', side_effect=command), \
                patch.object(launch, '_write_record', side_effect=write):
            self.assertEqual(launch._dispatch_first_frame(launch._FIRST_FRAME, args, proof,
                                                         lambda: events.append('profile verified')), 1)
        self.assertEqual(events, ['profile verified', (stream.FIRST, {'nonce': nonce, 'result': {'provisional': True, **proof}}),
                                  'settlement failed', (stream.FINAL, {'nonce': nonce, 'result': {'ok': False}})])

    def test_capability_and_dispatch_share_qualification_without_advertising_legacy_wheel(self):
        from vtx.cli import timeline_preview_window as cli
        from vtx import timeline

        proof = {'launcher': launch._LAUNCHER, 'wheel_sha256': 'a' * 64, 'runtime_projection_sha256': 'b' * 64}
        for supported, marker, warm, program in ((True, launch._INPUT_EPOCH_ABI, True, False),
                                                 (True, launch._INPUT_EPOCH_ABI, False, True),
                                                 (False, launch._INPUT_EPOCH_ABI, True, True),
                                                 (True, None, True, True)):
            capabilities = {'frame': {}, 'storage': {}}
            if supported:
                capabilities['preview_window'] = {'inline_media': True}
            with self.subTest(supported=supported, marker=marker, warm=warm, program=program), \
                    patch.object(timeline, 'timeline_capabilities', return_value={'capabilities': capabilities}), \
                    patch.object(cli, 'INPUT_EPOCH_ABI', marker), \
                    patch.object(cli, 'WARM_SVG_MEDIA_ABI', launch._WARM_SVG_MEDIA_ABI if warm else None), \
                    patch.object(cli, 'PROGRAM_AUDIO_ABI', launch._PROGRAM_AUDIO_ABI if program else None), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(launch._dispatch('capabilities', [], proof), 0)
            actual = json.loads(out.getvalue())['capabilities']
            expected = {'inline_media': True, 'input_epoch': True, **proof}
            if warm:
                expected['warm_svg_media'] = True
            if program:
                expected['program_audio'] = True
            self.assertEqual(actual, {'preview_window': expected} if supported and marker else {})

        with patch.object(cli, 'run_preview_window_command', return_value=0) as command:
            args = ['sentinel CLI argv from validated ingress']
            for op in ('preview-window', 'preview-warm-svg-media', 'preview-program-audio',
                       'preview-warm-svg-program-audio'):
                self.assertEqual(launch._dispatch(op, args, proof), 0)
                command.assert_called_once_with(args, qualification=proof)
                command.reset_mock()
            with patch.object(cli, 'WARM_SVG_MEDIA_ABI', None), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(launch._dispatch('preview-warm-svg-media', args, proof), 2)
                command.assert_not_called()
            with patch.object(cli, 'PROGRAM_AUDIO_ABI', None), contextlib.redirect_stderr(io.StringIO()):
                for op in ('preview-program-audio', 'preview-warm-svg-program-audio'):
                    self.assertEqual(launch._dispatch(op, args, proof), 2)
                command.assert_not_called()
