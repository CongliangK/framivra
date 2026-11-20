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
