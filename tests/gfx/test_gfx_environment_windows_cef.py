"""Windows CEF provenance with inert PE files; no browser or worker is launched."""
import copy
import json
import os
from pathlib import Path, PureWindowsPath
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests._support import temporary_root
from vtx.render import gfx_environment as env, gfx_environment_windows_cef as cef


def _image(*, dll=False, machine=0x8664):
    image = bytearray(512)
    image[:2] = b'MZ'
    struct.pack_into('<I', image, 60, 64)
    struct.pack_into('<4sHHIIIHHH', image, 64, b'PE\0\0', machine, 1, 0, 0, 0,
                     240, 2 | (0x2000 if dll else 0), 0x20B)
    return bytes(image)


class WindowsCefEnvironment(unittest.TestCase):
    def setUp(self):
        root = temporary_root(self).resolve()
        self.windows = root / 'Windows'
        self.windows.mkdir()
        self.publish = root / 'CEF worker — local'
        self.publish.mkdir()
        (self.publish / 'locales').mkdir()
        self.worker = self.publish / 'vtx_cef_worker.exe'
        self.libcef = self.publish / 'libcef.dll'
        self.resource = self.publish / 'resources.pak'
        self.locale = self.publish / 'locales/fr.pak'
        lock_path = Path(__file__).resolve().parents[2] / 'experiments/gfx_cef_worker/cef_lock.windows64.json'
        layout = json.loads(lock_path.read_text(encoding='utf-8'))['runtime']
        self.required = (*layout['Release'], *layout['Resources'], 'locales/en-US.pak')
        for name in ('vtx_cef_worker.exe', *self.required, 'locales/fr.pak'):
            p = self.publish / name
            is_dll = p.suffix == '.dll'
            p.write_bytes(_image(dll=is_dll) if is_dll or p.suffix == '.exe' else b'fixture')
        self.library = self.windows / 'dwrite.dll'
        self.font = self.windows / 'font.ttf'
        self.library.write_bytes(b'library')
        self.font.write_bytes(b'font')
        self.boot = '1969'
        self.settings = 'original'
        self.versions = []
        for p in (
                patch.dict(os.environ, {'VTX_GFX': 'cef', 'VTX_CEF_WORKER': str(self.worker),
                                        'SystemRoot': str(self.windows)}, clear=True),
                patch.object(env.platform, 'system', return_value='Windows'),
                patch.object(env.platform, 'machine', return_value='AMD64'),
                patch.object(cef, '_native_system', return_value=self.windows),
                patch.object(cef, '_require_local_drive'),
                patch.object(env, '_windows_version', side_effect=self.version),
                patch.object(env, '_windows_host', side_effect=self.host),
                patch.object(env.subprocess, 'run', side_effect=AssertionError('unexpected process launch'))):
            p.start()
            self.addCleanup(p.stop)

    def version(self, path):
        self.versions.append(path)
        return '154.0.57.0'

    def host(self, windows, *, cef=False):
        self.assertEqual(windows, self.windows)
        libraries = env._files([self.library])
        return {'host_boot': {'boot_filetime': self.boot, 'machine_guid': env.digest('machine')},
                'registry': {'fake registry key': env.digest(self.settings)},
                'libraries': libraries, 'graphics_libraries': libraries,
                'fonts': {'files': env._files([self.font]), 'configs': {'registry': env.digest(self.settings)},
                          'tools': libraries, 'libraries': libraries}, 'python_runtime': {'fixture': True}}

    def collect(self):
        return env.collect('cef', self.worker, 'a0')

    def test_binds_every_member_and_reenumerates_on_recheck(self):
        c = SimpleNamespace(w=64, h=48, fps=30)
        original = env.certificate(c)
        axes = original['axes']
        self.assertEqual(axes['platform_resolver'], 'windows-x64-cef/1')
        self.assertEqual(axes['pixel_equivalence'], 'unproven')
        self.assertEqual(axes['browser'], env._files([self.worker]))
        self.assertEqual(axes['version'], '154.0.57.0')
        self.assertEqual(self.versions, [self.libcef])
        env.recheck(c)
        extra = self.publish / 'SetupMetrics'; extra.mkdir()
        pma = extra / 'any.pma'; pma.write_bytes(b'A')
        with self.assertRaisesRegex(env.EnvironmentError, 'runtime/SetupMetrics/any.pma'):
            env.recheck(c)
        self.assertNotEqual(env.digest(axes['runtime']), env.digest(self.collect()['runtime']))
        pma.unlink(); extra.rmdir()
        for p in (self.worker, self.libcef, self.resource, self.locale, self.library, self.font):
            with self.subTest(member=p.name):
                data, info = p.read_bytes(), p.stat()
                p.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
                os.utime(p, ns=(info.st_atime_ns, info.st_mtime_ns))
                with self.assertRaisesRegex(env.EnvironmentError, 'changed inputs'):
                    env.recheck(c)
                p.write_bytes(data)
                self.assertEqual(env.certificate(c), original)
        env.recheck(c)

    def test_rechecks_host_settings_boot_and_normalized_cef_configuration(self):
        c = SimpleNamespace(w=64, h=48, fps=30)
        original = env.certificate(c)
        with patch.dict(os.environ, VTX_CEF_PNG_LEVEL='01'):
            self.assertEqual(self.collect()['cef_configuration'], original['axes']['cef_configuration'])
            env.recheck(c)
        for name, value in (('VTX_CEF_PNG_LEVEL', '2'), ('VTX_CEF_SYNC_MODE', 'p'), ('VTX_GFX_PAGES', '4')):
            with self.subTest(option=name), patch.dict(os.environ, {name: value}):
                with self.assertRaisesRegex(env.EnvironmentError, 'cef_configuration'):
                    env.recheck(c)
        self.settings = 'changed'
        with self.assertRaisesRegex(env.EnvironmentError, 'registry'):
            env.recheck(c)
        self.settings = 'original'
        self.boot = '1970'
        with self.assertRaisesRegex(env.EnvironmentError, 'host_boot/boot_filetime'):
            env.recheck(c)
        self.assertEqual(env.certificate(c), original)

    def test_certification_refuses_missing_resources_wrong_machine_mode_and_overrides(self):
        with self.assertRaisesRegex(env.EnvironmentError, 'requires A0'):
            env.collect('cef', self.worker, 'a1')
        with patch.dict(os.environ, VK_DRIVER_FILES='externally-selected.json'):
            with self.assertRaisesRegex(env.EnvironmentError, 'renderer environment override'):
                self.collect()
        with patch.dict(os.environ, VTX_CEF_ENCODE_THREADS='invalid'):
            self.assertEqual(env.resolve(SimpleNamespace())['status'], 'unknown')
        for name in self.required:
            p = self.publish / name
            data = p.read_bytes()
            p.unlink()
            with self.subTest(member=name):
                c = SimpleNamespace(w=64, h=48, fps=30)
                self.assertEqual(env.resolve(c)['status'], 'unknown')
                with self.assertRaisesRegex(env.EnvironmentError, 'reuse refused.*missing'):
                    env.certificate(c)
            p.write_bytes(data)
        self.libcef.write_bytes(_image(dll=True, machine=0xaa64))
        with self.assertRaisesRegex(env.EnvironmentError, r'AMD64 PE32\+ DLL'):
            self.collect()
        self.libcef.write_bytes(_image(dll=True))
        self.worker.write_bytes(b'MZ')
        with self.assertRaisesRegex(env.EnvironmentError, r'AMD64 PE32\+ executable'):
            self.collect()

    def test_reparse_and_changed_distribution_inventory_are_rejected(self):
        original_lstat = Path.lstat
        for target in (self.resource, self.locale.parent):
            def reparse(p, *args, **kwargs):
                info = original_lstat(p, *args, **kwargs)
                if p == target:
                    return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
                return info
            with self.subTest(member=target.name), patch.object(Path, 'lstat', reparse):
                with self.assertRaisesRegex(env.EnvironmentError, 'reparse'):
                    self.collect()
        hash_file = env._file_resolved
        def add_member(path, info, **kw):
            result = hash_file(path, info, **kw)
            if path == self.resource:
                (self.publish / 'appeared-during-scan.pak').write_bytes(b'new')
            return result
        with patch.object(env, '_file_resolved', side_effect=add_member):
            with self.assertRaisesRegex(env.EnvironmentError, 'directory changed while hashing'):
                self.collect()

    def test_recheck_rejects_retargeted_selector_without_adopting_it(self):
        c = SimpleNamespace(w=64, h=48, fps=30)
        old = env.certificate(c)
        other = self.publish / 'other' / self.worker.name
        other.parent.mkdir()
        other.write_bytes(_image())
        with patch('vtx.render.gfx_backend.cef_config', return_value=(other, 'a0')):
            with self.assertRaisesRegex(env.EnvironmentError, 'browser'):
                env.recheck(c)
        self.assertEqual(env.certificate(c), old)

    def test_wire_validation_requires_consistent_windows_cef_axes(self):
        axes = self.collect()
        # On POSIX fixtures use the same disk facts translated to Windows wire paths.
        runtime = next(iter(axes['runtime'].values()))
        root = PureWindowsPath('C:/CEF worker — local')
        runtime = {name: [str(root / name), entry[1]] for name, entry in runtime.items()}
        axes.update(runtime={str(root): runtime}, browser=dict([runtime['vtx_cef_worker.exe']]),
                    viewport={'width': 64, 'height': 48}, fps=30)
        axes['windows_loader'].update(application=str(root), system32=r'C:\Windows\System32')
        def certificate(data):
            return {'schema': env.SCHEMA, 'status': 'input-bound', 'sha256': env.digest(data), 'axes': data}
        self.assertEqual(env.verify_certificate(certificate(axes)), env.digest(axes))
        for field in ('cef_configuration', 'architecture', 'windows_loader', 'host_boot', 'registry',
                      'launch_flags', 'graphics_libraries', 'browser'):
            changed = copy.deepcopy(axes)
            del changed[field]
            with self.subTest(field=field), self.assertRaises(env.EnvironmentError):
                env.verify_certificate(certificate(changed))
        del runtime['locales/en-US.pak']
        with self.assertRaisesRegex(env.EnvironmentError, 'incomplete Windows CEF'):
            env.verify_certificate(certificate(axes))

    def test_cdp_and_cli_keep_existing_resolver_and_launch_flags(self):
        app = self.publish.parent / 'Application'; app.mkdir()
        chrome = app / 'chrome.exe'; chrome.write_bytes(_image())
        version = app / '154.0.57.0'; version.mkdir()
        (version / 'chrome.dll').write_bytes(b'fixture')
        for backend in ('cdp', 'cli'):
            with self.subTest(backend=backend):
                axes = env.collect(backend, chrome)
                self.assertEqual(axes['platform_resolver'], 'windows-x64/1')
                self.assertEqual(axes['launch_flags'], list(env.CHROME_VISUAL_FLAGS))
                self.assertNotIn('cef_configuration', axes)
                self.assertNotIn('windows_loader', axes)
                self.assertEqual(axes['browser'], env._files([chrome]))
                with self.assertRaises(env.EnvironmentError):
                    env.collect(backend, self.worker)
                with self.assertRaises(env.EnvironmentError):
                    env.collect(backend, chrome, 'a0')
