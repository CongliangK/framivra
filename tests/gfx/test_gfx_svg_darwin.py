"""Darwin SVG resolver contracts with real synthetic Mach-O files, no renderer.

These are loader/identity unit tests, never evidence of real Inkscape pixels.
"""
import contextlib
import os
import plistlib
import stat
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vtx.render import gfx_svg
from vtx.render import gfx_environment


def macho(path, *, deps=(), rpaths=(), filetype=6, cpu=0x100000c):
    commands = []
    for kind, values in ((0xc, deps), (0x8000001c, rpaths)):
        for value in values:
            body = value.encode() + b'\0'
            size = (12 + len(body) + 7) // 8 * 8
            commands.append(struct.pack('<III', kind, size, 12) + body + b'\0' * (size - 12 - len(body)))
    payload = b''.join(commands)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack('<8I', 0xfeedfacf, cpu, 0, filetype, len(commands), len(payload), 0, 0) + payload)
    return path


class DarwinSvgIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.app = self.root / 'Inkscape.app'
        self.main = self.app / 'Contents/MacOS/inkscape'
        self.resources = self.app / 'Contents/Resources'
        (self.resources / 'share/inkscape').mkdir(parents=True)
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'org.inkscape.Inkscape', 'CFBundleExecutable': 'inkscape'}))
        macho(self.main, filetype=2)

    @contextlib.contextmanager
    def runtime(self):
        contains = mock.Mock(side_effect=lambda p: p.startswith(b'/usr/lib/'))
        original_file = gfx_environment._file
        def digest(path):
            return ('/usr/lib/dyld', 'd' * 64) if str(path) == '/usr/lib/dyld' else original_file(path)
        with mock.patch.object(gfx_svg.platform, 'machine', return_value='arm64'), \
             mock.patch.object(gfx_svg.ctypes, 'CDLL', return_value=SimpleNamespace(_dyld_shared_cache_contains_path=contains)), \
             mock.patch('vtx.render.gfx_environment._darwin_dyld', return_value=('cache', b'uuid')), \
             mock.patch('vtx.render.gfx_environment._darwin_cache', return_value={'uuid': 'fixture', 'files': {}}), \
             mock.patch('vtx.render.gfx_environment._file', side_effect=digest):
            yield

    def test_loader_closure_and_distribution_bytes_are_bound(self):
        lib = self.resources / 'lib/libpaint.dylib'
        macho(lib, deps=('/usr/lib/libSystem.B.dylib',))
        macho(self.main, deps=('@rpath/libpaint.dylib',), rpaths=('@executable_path/../Resources/lib',), filetype=2)
        resource = self.resources / 'share/inkscape/colors.xml'
        resource.write_text('one')
        with self.runtime():
            first = gfx_svg._darwin_runtime(self.main)
            self.assertEqual(first['dependencies'][str(self.main)]['@rpath/libpaint.dylib'], str(lib))
            self.assertIn(str(lib), first['files'])
            resource.write_text('two')
            self.assertNotEqual(first, gfx_svg._darwin_runtime(self.main))
            first = gfx_svg._darwin_runtime(self.main)
            lib.write_bytes(lib.read_bytes() + b'changed library bytes')
            self.assertNotEqual(first, gfx_svg._darwin_runtime(self.main))

    def test_bundle_fast_path_binds_canonical_files_and_internal_links(self):
        from hashlib import sha256
        resource = self.resources / 'share/inkscape/resource.dat'
        resource.write_bytes(b'bound resource')
        alias = self.resources / 'resource-alias'
        alias.symlink_to('share/inkscape/resource.dat')
        with mock.patch.object(gfx_environment, '_file',
                               side_effect=AssertionError('bundle repeated generic resolution')):
            first = gfx_environment._darwin_bundle(self.app)
        self.assertEqual(first['files']['Contents/Resources/resource-alias'],
                         [str(resource.resolve()), sha256(b'bound resource').hexdigest()])
        self.assertEqual(first['links']['Contents/Resources/resource-alias'],
                         ['share/inkscape/resource.dat', 'Contents/Resources/share/inkscape/resource.dat'])
        resource.write_bytes(b'changed resource')
        self.assertNotEqual(first, gfx_environment._darwin_bundle(self.app))

    def test_bundle_still_rejects_external_member_links(self):
        outside = self.root / 'outside.dat'
        outside.write_bytes(b'outside')
        (self.resources / 'outside-alias').symlink_to(outside)
        with self.assertRaisesRegex(gfx_environment.EnvironmentError, 'escapes'):
            gfx_environment._darwin_bundle(self.app)

    def test_bundle_membership_alias_and_restored_mtime_are_rechecked(self):
        resource = self.resources / 'share/inkscape/resource.dat'
        resource.write_bytes(b'first')
        app_alias = self.root / 'Alias.app'
        app_alias.symlink_to(self.app.name, target_is_directory=True)
        first = gfx_environment._darwin_bundle(self.app)
        self.assertEqual(first, gfx_environment._darwin_bundle(app_alias))
        added = resource.with_name('added.dat')
        added.write_bytes(b'new member')
        self.assertNotEqual(first, gfx_environment._darwin_bundle(self.app))
        added.unlink()
        self.assertEqual(first, gfx_environment._darwin_bundle(self.app))
        saved = resource.stat()
        resource.write_bytes(b'other')
        os.utime(resource, ns=(saved.st_atime_ns, saved.st_mtime_ns))
        self.assertNotEqual(first, gfx_environment._darwin_bundle(self.app))
        resource.unlink()
        self.assertNotEqual(first, gfx_environment._darwin_bundle(self.app))

    def test_bundle_directory_prefix_is_guarded_against_member_mutation(self):
        original = gfx_environment._file_resolved
        added = self.main.parent / 'added-during-inventory'
        def hashed(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == self.main:
                saved = path.parent.stat()
                added.write_bytes(b'new member')
                os.utime(path.parent, ns=(saved.st_atime_ns, saved.st_mtime_ns))
            return result
        with mock.patch.object(gfx_environment, '_file_resolved', side_effect=hashed):
            with self.assertRaisesRegex(ValueError, 'directory changed'):
                gfx_environment._darwin_bundle(self.app)

    def test_bundle_directory_guards_do_not_use_regular_file_handle_tokens(self):
        expected = gfx_environment._darwin_bundle(self.app)
        original = gfx_environment._file_token
        checked_files = []
        def file_only(path, info):
            # Simulate Windows _file_token's lack of directory-handle support
            # without changing os.name (and the host pathlib implementation).
            if stat.S_ISDIR(info.st_mode):
                raise PermissionError('regular-file token cannot open directory')
            checked_files.append(str(path))
            return original(path, info)
        with mock.patch.object(gfx_environment, '_file_token', side_effect=file_only):
            self.assertEqual(expected, gfx_environment._darwin_bundle(self.app))
        self.assertIn(str(self.main), checked_files)

    def test_bundle_cached_member_checks_full_token_after_lookup(self):
        gfx_environment._darwin_bundle(self.app)
        original = Path.lstat
        calls = 0
        def current(path):
            nonlocal calls
            if path == self.main:
                calls += 1
                if calls == 2:
                    saved = path.stat()
                    raw = path.read_bytes()
                    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
                    os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
            return original(path)
        with mock.patch.object(Path, 'lstat', new=current):
            with self.assertRaisesRegex(ValueError, 'member changed'):
                gfx_environment._darwin_bundle(self.app)

    def test_bundle_directory_link_retarget_and_dangling_link_fail(self):
        alias = self.resources / 'resource-directory'
        alias.symlink_to('share', target_is_directory=True)
        first = gfx_environment._darwin_bundle(self.app)
        alias.unlink()
        alias.symlink_to('share/inkscape', target_is_directory=True)
        self.assertNotEqual(first, gfx_environment._darwin_bundle(self.app))
        alias.unlink()
        alias.symlink_to('missing', target_is_directory=True)
        with self.assertRaises(OSError):
            gfx_environment._darwin_bundle(self.app)

    def test_runtime_parses_each_file_once_but_keeps_loader_contexts(self):
        shared = macho(self.resources / 'lib/gio/modules/shared.dylib')
        macho(self.main, deps=(str(shared), str(shared)), filetype=2)
        original = gfx_environment._darwin_load_commands
        with self.runtime(), mock.patch.object(gfx_environment, '_darwin_load_commands',
                                                wraps=original) as parsed:
            first = gfx_svg._darwin_runtime(self.main)
        self.assertEqual([call.args[0] for call in parsed.call_args_list].count(shared), 1)
        self.assertEqual([call.args[0] for call in parsed.call_args_list].count(self.main), 1)
        with self.runtime():
            self.assertEqual(first, gfx_svg._darwin_runtime(self.main))
            saved = shared.stat()
            # Same-size load command mutation must not reuse previous facts.
            macho(shared, cpu=0x1000007)
            os.utime(shared, ns=(saved.st_atime_ns, saved.st_mtime_ns))
            with self.assertRaisesRegex(ValueError, 'non-native'):
                gfx_svg._darwin_runtime(self.main)

    def test_runtime_repeated_file_mutation_is_refused_with_restored_mtime(self):
        shared = macho(self.resources / 'lib/shared.dylib')
        mutator = macho(self.resources / 'lib/mutator.dylib')
        macho(self.main, deps=(str(shared), str(mutator), str(shared)), filetype=2)
        original = gfx_environment._darwin_load_commands
        def parsed(path):
            result = original(path)
            if path == mutator:
                saved = shared.stat()
                macho(shared, cpu=0x1000007)
                os.utime(shared, ns=(saved.st_atime_ns, saved.st_mtime_ns))
            return result
        with self.runtime(), mock.patch.object(gfx_environment, '_darwin_load_commands', side_effect=parsed):
            with self.assertRaisesRegex(ValueError, 'changed during inventory'):
                gfx_svg._darwin_runtime(self.main)

    def test_context_dependent_shared_library_still_fails(self):
        shared = macho(self.resources / 'lib/shared.dylib', deps=('@rpath/leaf.dylib',))
        parents = []
        for name in ('a', 'b'):
            parent = macho(self.resources / name / 'parent.dylib',
                           deps=(str(shared),), rpaths=('@loader_path',))
            macho(parent.parent / 'leaf.dylib')
            parents.append(str(parent))
        macho(self.main, deps=parents, filetype=2)
        with self.runtime(), self.assertRaisesRegex(ValueError, 'context-dependent'):
            gfx_svg._darwin_runtime(self.main)

    def test_module_root_boundary_and_load_command_mutations_are_bound(self):
        module = macho(self.resources / 'lib/pango')
        inactive = macho(self.resources / 'lib/pango-extra/ignored.dylib', cpu=0x1000007)
        with self.runtime():
            first = gfx_svg._darwin_runtime(self.main)
            self.assertIn(str(module), first['dependencies'])
            self.assertNotIn(str(inactive), first['dependencies'])
            macho(module, deps=('/usr/lib/libSystem.B.dylib',))
            second = gfx_svg._darwin_runtime(self.main)
            self.assertNotEqual(first, second)
            self.assertEqual(second['dependencies'][str(module)],
                             {'/usr/lib/libSystem.B.dylib': 'system-cache'})
            module.unlink()
            self.assertNotIn(str(module), gfx_svg._darwin_runtime(self.main)['dependencies'])

    def test_runtime_app_and_executable_aliases_are_canonical(self):
        app_alias = self.root / 'Alias.app'
        app_alias.symlink_to(self.app.name, target_is_directory=True)
        executable_alias = self.root / 'inkscape-alias'
        executable_alias.symlink_to(self.main)
        with self.runtime():
            first = gfx_svg._darwin_runtime(self.main)
            self.assertEqual(first, gfx_svg._darwin_runtime(app_alias / 'Contents/MacOS/inkscape'))
            self.assertEqual(first, gfx_svg._darwin_runtime(executable_alias))

    def test_standalone_helper_executable_path_is_its_own(self):
        helper = self.resources / 'lib/gio/modules/helper'
        lib = self.resources / 'lib/gio/modules/libhelper.dylib'
        macho(helper, deps=('@executable_path/libhelper.dylib',), filetype=2)
        macho(lib)
        with self.runtime():
            record = gfx_svg._darwin_runtime(self.main)
        self.assertEqual(record['dependencies'][str(helper)]['@executable_path/libhelper.dylib'], str(lib))

    def test_inactive_intel_resource_is_bound_but_active_intel_is_rejected(self):
        inactive = macho(self.resources / 'lib/python/inactive.dylib', cpu=0x1000007)
        with self.runtime():
            first = gfx_svg._darwin_runtime(self.main)
            self.assertNotIn(str(inactive), first['dependencies'])
            inactive.write_bytes(inactive.read_bytes() + b'inactive changed')
            self.assertNotEqual(first, gfx_svg._darwin_runtime(self.main))
            macho(self.main, deps=(str(inactive),), filetype=2)
            with self.assertRaisesRegex(ValueError, 'native'):
                gfx_svg._darwin_runtime(self.main)

    def test_inherited_runpath_and_external_fontconfig_closure(self):
        main = macho(self.root / 'bin/fc-scan', deps=('@rpath/libfc.dylib',), rpaths=('@executable_path/../lib',), filetype=2)
        lib = macho(self.root / 'lib/libfc.dylib', deps=('@rpath/libpaint.dylib',))
        leaf = macho(self.root / 'lib/libpaint.dylib')
        with self.runtime():
            record = gfx_svg._darwin_runtime(main)
        self.assertEqual(record['dependencies'][str(lib)]['@rpath/libpaint.dylib'], str(leaf))

    def test_ambiguous_missing_external_and_incompatible_dependencies_fail(self):
        for mode in ('ambiguous', 'missing', 'external', 'intel'):
            with self.subTest(mode=mode):
                first = self.resources / mode / 'libpaint.dylib'
                second = self.resources / (mode + '2') / 'libpaint.dylib'
                if mode != 'missing':
                    macho(first, cpu=0x1000007 if mode == 'intel' else 0x100000c)
                if mode == 'ambiguous':
                    macho(second)
                if mode == 'external':
                    dep = str(macho(self.root / 'outside/libpaint.dylib'))
                else:
                    dep = '@rpath/libpaint.dylib'
                macho(self.main, deps=(dep,), rpaths=(str(first.parent), str(second.parent)), filetype=2)
                with self.runtime(), self.assertRaises((ValueError, OSError)):
                    gfx_svg._darwin_runtime(self.main)

    def test_app_real_binary_override_and_launcher_rejection(self):
        with mock.patch.object(gfx_svg.sys, 'platform', 'darwin'), \
             mock.patch.dict(gfx_svg.os.environ, {'VTX_INKSCAPE': str(self.main)}):
            self.assertEqual(gfx_svg.inkscape_executable(), str(self.main))
            self.main.write_text('#!/bin/sh\nexec arbitrary-launcher\n')
            with self.assertRaisesRegex(ValueError, 'Mach-O'):
                gfx_svg.inkscape_executable()

    def test_private_profile_disables_distribution_fonts_and_inherited_overrides(self):
        private = self.root / 'private'
        gfx_svg.private_configuration(private)
        import xml.etree.ElementTree as ET
        font = ET.parse(private / 'inkscape-profile/preferences.xml').find('.//group[@id="font"]')
        self.assertEqual(font.get('use_fontsdir_system'), '0')
        self.assertEqual(font.get('use_fontsdir_user'), '0')
        with mock.patch.dict(gfx_svg.os.environ, {'DYLD_LIBRARY_PATH': '/untrusted', 'FONTCONFIG_FILE': '/untrusted'}):
            env = gfx_svg.process_environment(private)
        self.assertNotIn('DYLD_LIBRARY_PATH', env)
        self.assertEqual(env['PANGOCAIRO_BACKEND'], 'fc')
        self.assertEqual(env['FONTCONFIG_FILE'], str(private / 'fonts.conf'))

    def test_introspection_has_temporary_home_and_cleans_up(self):
        paths = []
        def inspect(argv, **kwargs):
            private = Path(kwargs['env']['HOME']).parent
            paths.append(private)
            self.assertTrue((private / 'fonts.conf').is_file())
            self.assertTrue((private / 'inkscape-profile/preferences.xml').is_file())
            return SimpleNamespace(returncode=0, stdout='Inkscape fixture')
        with mock.patch.object(gfx_svg.subprocess, 'run', side_effect=inspect):
            gfx_svg._inspect_command(['/fixture/inkscape', '--version'])
        self.assertFalse(paths[0].exists())

    def test_linux_loader_identity_remains_file_bound(self):
        library = self.root / 'libgraphics.so'; library.write_bytes(b'graphics')
        loader = self.root / 'ld-linux.so'; loader.write_bytes(b'loader')
        output = f'libgraphics.so => {library} (0x1234)\n{loader} (0x5678)\n'
        with mock.patch.object(gfx_svg.sys, 'platform', 'linux'), \
             mock.patch.object(gfx_svg.shutil, 'which', return_value='/usr/bin/ldd'), \
             mock.patch.object(gfx_svg.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=output)):
            first = gfx_svg.runtime_libraries('/fixture/inkscape')
            library.write_bytes(b'graphics changed')
            self.assertNotEqual(first, gfx_svg.runtime_libraries('/fixture/inkscape'))
            self.assertIn(loader.name, first)


if __name__ == '__main__':
    unittest.main()
