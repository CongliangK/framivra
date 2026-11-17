"""Focused frame environment falsification; fixtures never initialize fonts."""
import ctypes
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from vtx.render import frame_environment as frame
from vtx.render import gfx_environment as env
from vtx import timeline_frame


def macho(deps=(), rpaths=(), kind=6, cpu=0x100000c):
    commands = []
    for command, names in ((0xc, deps), (0x8000001c, rpaths)):
        for name in names:
            value = name.encode() + b'\0'
            length = (12 + len(value) + 7) // 8 * 8
            commands.append(struct.pack('<III', command, length, 12)
                            + value + b'\0' * (length - 12 - len(value)))
    data = b''.join(commands)
    return struct.pack('<8I', 0xfeedfacf, cpu, 0, kind, len(commands), len(data), 0, 0) + data


class FrameEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.binary = self.put('bin/ffmpeg', macho(kind=2))
        self.libass = self.put('lib/libass.9.dylib', macho())

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def closure(self):
        return frame._dependencies(self.binary, lambda p: p == b'/usr/lib/libSystem.B.dylib')

    def test_recursive_external_loader_executable_and_inherited_rpath_closure(self):
        self.binary.write_bytes(macho(['@rpath/liba.dylib'], ['@executable_path/../lib'], kind=2))
        a = self.put('lib/liba.dylib', macho(['@loader_path/libb.dylib']))
        b = self.put('lib/libb.dylib', macho(['@rpath/libc.dylib']))
        c = self.put('lib/libc.dylib', macho(['/usr/lib/libSystem.B.dylib']))
        first = self.closure()
        self.assertEqual(set(first['files']), set(map(str, (self.binary, a, b, c))))
        self.assertEqual(first['dependencies'][str(c)]['loads']['/usr/lib/libSystem.B.dylib'], 'system-cache')
        c.write_bytes(c.read_bytes() + b'changed external dependency')
        self.assertNotEqual(first, self.closure())
        b.unlink()
        with self.assertRaisesRegex(env.EnvironmentError, 'unresolved'):
            self.closure()

    def test_rpath_first_existing_and_preceding_missing_candidate_rechecked(self):
        self.binary.write_bytes(macho(['@rpath/liba.dylib'],
            ['@executable_path/../first', '@executable_path/../second'], kind=2))
        second = self.put('second/liba.dylib', macho())
        first = self.closure()
        self.assertIn(str(second), first['files'])
        earlier = self.put('first/liba.dylib', macho())
        after = self.closure()
        self.assertNotEqual(first, after)
        self.assertIn(str(earlier), after['files'])
        self.assertNotIn(str(second), after['files'])

    def test_dependency_cycle_terminates(self):
        a = self.put('lib/a.dylib', macho(['@loader_path/b.dylib']))
        self.put('lib/b.dylib', macho(['@loader_path/a.dylib']))
        self.binary.write_bytes(macho([str(a)], kind=2))
        self.assertEqual(len(self.closure()['files']), 3)

    def test_ambiguous_loader_context_rejected(self):
        shared = self.put('lib/shared.dylib', macho(['@rpath/leaf.dylib']))
        a = self.put('a/parent.dylib', macho([str(shared)], ['@loader_path']))
        b = self.put('b/parent.dylib', macho([str(shared)], ['@loader_path']))
        self.put('a/leaf.dylib', macho())
        self.put('b/leaf.dylib', macho())
        self.binary.write_bytes(macho([str(a), str(b)], kind=2))
        with self.assertRaisesRegex(env.EnvironmentError, 'ambiguous'):
            self.closure()

    def test_unknown_architecture_and_relative_paths_fail_closed(self):
        for data in (macho(cpu=0x1000007, kind=2), macho(['relative.dylib'], kind=2),
                     macho(['@rpath/absent.dylib'], kind=2), b'not Mach-O'):
            with self.subTest(data=data):
                self.binary.write_bytes(data)
                with self.assertRaises(env.EnvironmentError):
                    self.closure()

    def test_system_cache_membership_is_required(self):
        self.binary.write_bytes(macho(['/usr/lib/not-a-cache-library.dylib'], kind=2))
        with self.assertRaisesRegex(env.EnvironmentError, 'unresolved'):
            self.closure()

    def test_embedded_loader_environment_rejected(self):
        command = struct.pack('<III', 0x27, 24, 12) + b'DYLD_X=evil\0\0'
        self.binary.write_bytes(struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, 1,
                                           len(command), 0, 0) + command)
        with self.assertRaisesRegex(env.EnvironmentError, 'embedded'):
            self.closure()

    def test_compiled_provider_query_never_initializes_renderer_or_fontconfig(self):
        for providers in ([0, 1, 2], [0, 1, 3], [0, 1, 2, 3], [0, 1], [0, 1, 99]):
            with self.subTest(providers=providers):
                array = (ctypes.c_int * len(providers))(*providers)
                def query(handle, output, size):
                    ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(ctypes.c_int)))[0] = array
                    ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))[0] = len(providers)
                library = SimpleNamespace(ass_library_init=Mock(return_value=1),
                    ass_library_done=Mock(), ass_get_available_font_providers=Mock(side_effect=query))
                process = SimpleNamespace(free=Mock())
                with patch.object(frame.ctypes, 'CDLL', side_effect=lambda p: process if p is None else library):
                    if providers == [0, 1, 2]:
                        self.assertEqual(frame._coretext_provider({'files': {str(self.libass): ''}})['selected'], 'coretext')
                    else:
                        with self.assertRaisesRegex(env.EnvironmentError, 'sole CoreText'):
                            frame._coretext_provider({'files': {str(self.libass): ''}})
                library.ass_library_done.assert_called_once_with(1)
                process.free.assert_called_once()

    def test_unknown_libass_provider_api_rejected(self):
        with patch.object(frame.ctypes, 'CDLL', return_value=SimpleNamespace()):
            with self.assertRaisesRegex(env.EnvironmentError, 'provider API'):
                frame._coretext_provider({'files': {str(self.libass): ''}})
        with self.assertRaisesRegex(env.EnvironmentError, 'one dynamically linked'):
            frame._coretext_provider({'files': {}})

    def test_override_and_host_arch_rejected_before_any_native_probe(self):
        for key in ('DYLD_INSERT_LIBRARIES', '__XPC_DYLD_LIBRARY_PATH', 'LD_LIBRARY_PATH',
                    'FONTCONFIG_FILE', 'FONTCONFIG_PATH', 'FONTCONFIG_SYSROOT', 'CFFIXED_USER_HOME'):
            with self.subTest(key=key), patch.dict(os.environ, {key: 'override'}, clear=True), \
                    patch.object(frame.platform, 'machine', return_value='arm64'), \
                    patch.object(env, '_darwin_dyld') as probe:
                with self.assertRaisesRegex(env.EnvironmentError, 'override'):
                    frame._darwin(self.binary)
                probe.assert_not_called()
        with patch.object(frame.platform, 'machine', return_value='x86_64'), \
                patch.object(env, '_darwin_dyld') as probe:
            with self.assertRaisesRegex(env.EnvironmentError, 'arm64'):
                frame._darwin(self.binary)
            probe.assert_not_called()

    def test_cli_preference_domain_uses_actual_basename_and_rejects_app_wrappers(self):
        alias = self.root / 'renamed-ffmpeg'
        alias.symlink_to(self.binary)
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(frame.platform, 'machine', return_value='arm64'), \
                patch.object(env, '_darwin_dyld', return_value=('cache', b'uuid')) as dyld, \
                patch.object(frame.ctypes, 'CDLL', return_value=SimpleNamespace(
                    _dyld_shared_cache_contains_path=Mock())), \
                patch.object(frame, '_dependencies', return_value={}), \
                patch.object(frame, '_coretext_provider', return_value={'selected': 'coretext'}), \
                patch.object(env, '_darwin_cache', return_value={'files': {}}), \
                patch.object(env, '_darwin_fonts', return_value={}) as fonts, \
                patch.object(env, '_files', return_value={}):
            frame._darwin(alias)
            fonts.assert_called_once_with({}, ['ffmpeg', 'renamed-ffmpeg'])
            wrapped = self.put('Tool.app/Contents/MacOS/ffmpeg', macho(kind=2))
            dyld.reset_mock()
            with self.assertRaisesRegex(env.EnvironmentError, 'app-wrapped'):
                frame._darwin(wrapped)
            dyld.assert_not_called()
            outside_alias = self.root / 'outside-alias'
            outside_alias.symlink_to(wrapped)
            with self.assertRaisesRegex(env.EnvironmentError, 'app-wrapped'):
                frame._darwin(outside_alias)
            dyld.assert_not_called()

    def test_coretext_font_bytes_membership_fallback_config_and_preferences_bound(self):
        font = self.put('font.otf', b'font A')
        resource = self.put('CoreText/Resources/fallback.plist', b'fallback A')
        preference = self.put('Library/Preferences/com.apple.CoreText.plist', b'preference A')
        application = self.put('Library/Preferences/ffmpeg.plist', b'application A')
        urls = [str(font)]
        def collect():
            return env._darwin_fonts({'cache': 'hash'}, ['ffmpeg'])
        with patch.dict(os.environ, {'HOME': str(self.root)}, clear=True), \
                patch.object(env, '_CORETEXT', str(resource.parent.parent)), \
                patch.object(env, '_darwin_font_inventory', side_effect=lambda: (list(urls), ['Fixture'])):
            original = collect()
            for path in (font, resource, preference, application):
                before = collect()
                path.write_bytes(path.read_bytes() + b'changed')
                self.assertNotEqual(before, collect())
            urls.append(str(self.put('second.otf', b'font B')))
            self.assertNotEqual(original, collect())

    def test_linux_uses_unchanged_existing_collectors(self):
        with patch.object(frame.sys, 'platform', 'linux'), \
                patch.object(env, '_file', return_value=('ffmpeg', 'hash')) as file, \
                patch.object(env, '_libraries', return_value={'lib': 'hash'}) as libs, \
                patch.object(env, '_fonts', return_value={'font': 'hash'}) as fonts, \
                patch.object(env, '_files', return_value={'implementation': 'hash'}), \
                patch.object(env, '_darwin_font_inventory', side_effect=AssertionError('Darwin probe')):
            value = frame.collect('ffmpeg')
        file.assert_called_once_with('ffmpeg')
        libs.assert_called_once_with('ffmpeg')
        fonts.assert_called_once_with()
        self.assertEqual(value['libraries'], {'lib': 'hash'})
        self.assertEqual(value['fonts'], {'font': 'hash'})

    def test_frame_override_rejected_before_ctx_or_media_probe_and_without_report(self):
        import wave
        from tests.contracts.test_timeline_frame import document
        source = self.root / 'source.wav'
        with wave.open(str(source), 'wb') as stream:
            stream.setparams((1, 2, 48000, 480, 'NONE', 'not compressed'))
            stream.writeframes(b'\0' * 960)
        doc = document()
        doc['sources'][0]['path'] = source.name
        path = self.put('timeline.json', json.dumps(doc).encode())
        report = self.root / 'forbidden-report.log'
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        for platform, key, value in (('linux', 'FFREPORT', 'file=' + str(report)),
                                     ('darwin', 'DYLD_INSERT_LIBRARIES', '/injected.dylib')):
            with self.subTest(platform=platform), patch.dict(os.environ, {key: value}, clear=True), \
                    patch.object(frame.sys, 'platform', platform), \
                    patch.object(frame.platform, 'machine', return_value='arm64'), \
                    patch.object(timeline_frame.shutil, 'which', return_value=str(self.binary)), \
                    patch('vtx.runtime.fstxn.directory_write_capability', return_value=True), \
                    patch('vtx.pluginlib.hooks.active_plugins', return_value=[]), \
                    patch('vtx.pipeline.infra.Ctx.__init__', side_effect=AssertionError('Ctx')) as ctx, \
                    patch('subprocess.run', side_effect=AssertionError('probe')) as probe, \
                    patch('subprocess.Popen', side_effect=AssertionError('probe')) as popen:
                result = timeline_frame.frame_timeline(path, frame=1, render=True)
                self.assertFalse(result['ok'])
                self.assertIsNone(result['image'])
                self.assertIn('unsupported', result['diagnostics'][0]['message'])
                ctx.assert_not_called()
                probe.assert_not_called()
                popen.assert_not_called()
            self.assertFalse(report.exists())
            self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))

    def test_timeline_delegates_and_keeps_report_injection_rejection(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(timeline_frame.shutil, 'which', return_value='/ffmpeg'), \
                patch.object(frame, 'collect', return_value={'fixture': True}) as collect:
            self.assertEqual(timeline_frame._environment(), {'fixture': True})
            collect.assert_called_once_with('/ffmpeg')
            for key in ('FFREPORT', 'LD_AUDIT', 'LD_PRELOAD'):
                with patch.dict(os.environ, {key: 'bad'}):
                    with self.assertRaisesRegex(ValueError, 'injection/FFREPORT'):
                        timeline_frame._environment()
            self.assertEqual(collect.call_count, 1)


if __name__ == '__main__':
    unittest.main()
