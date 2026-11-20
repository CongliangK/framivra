import contextlib
import io
import json
import os
from pathlib import Path
import stat
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


def archive():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as wheel:
        for name, data in _MEMBERS.items():
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
