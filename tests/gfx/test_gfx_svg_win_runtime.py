"""Offline checks for the owned Windows SVG project's private runtime scopes."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from tests._support import temporary_root
from tests.gfx._png import png
from vtx.render import gfx_svg as svg, gfx_svg_win_runtime as win


class WindowsSvgProjectRuntime(unittest.TestCase):
    def test_generic_font_tuples_keep_order_but_standard_requires_exact_set(self):
        names = ('fonts/项目.OTF', 'fonts/second.ttc')
        self.assertEqual(win._font_tuple(names, standard=False), names)
        self.assertEqual(win._font_tuple(win._FONT_PATHS, standard=True), win._FONT_PATHS)
        with self.assertRaisesRegex(ValueError, 'ordered portable-v2'):
            win._font_tuple(win._FONT_PATHS[::-1], standard=True)
        for inputs in ((names[0], names[0]), ('../local.ttf',), ('C:/Fonts/Arial.ttf',), ('local.svg',)):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                win._font_tuple(inputs, standard=False)

    def test_reuses_verified_package_but_not_shell_or_catalog_between_tuples(self):
        root = temporary_root(self)
        bundle = root / 'bundle'
        vendor = bundle / 'inkscape'
        first_shell = bundle / 'shell'
        first_shell.mkdir(parents=True)
        op = SimpleNamespace(inputs=SimpleNamespace(vendor=vendor, cwd=first_shell,
                                                     executable=vendor / 'bin' / 'inkscape.com'), keep=False)
        ctx = SimpleNamespace(root=root)
        images = object()
        recheck = mock.Mock()

        def environment(cwd, _):
            for leaf in ('fonts', 'font-cache', 'profile'):
                (cwd / leaf).mkdir()
            (cwd / 'fonts.conf').write_bytes(cwd.as_posix().encode())
            (cwd / 'profile' / 'preferences.xml').write_bytes(b'private profile')
            return object()

        def stage(data, sha, _, target, _pins):
            self.assertEqual(svg.digest(data), sha)
            target.write_bytes(data)

        def project_catalog(bundle_arg, cwd, staged, **kwargs):
            self.assertEqual(bundle_arg, bundle)
            self.assertIs(kwargs['operation'], op)
            self.assertEqual({target for target, _ in staged}, set((cwd / 'fonts').iterdir()))
            return [[{'family': target.read_text(encoding='ascii'), 'style': 'Regular', 'index': 0}]
                    for target, sha in staged if svg.digest(target.read_bytes()) == sha]

        def pin_entry(path):
            return nullcontext((path, None, None, False))

        with ExitStack() as pins:
            def patch(obj, name, **kwargs):
                return pins.enter_context(mock.patch.object(obj, name, **kwargs))

            patch(win.snapshot, '_pin_dir', side_effect=lambda path: nullcontext(path))
            patch(win.snapshot, '_pin_entry', side_effect=pin_entry)
            patch(win.shell, '_read_pinned', side_effect=lambda path, *_: path.read_bytes())
            patch(win.shell, '_environment', side_effect=environment)
            patch(win.shell, '_stage_font', side_effect=stage)
            patch(svg, 'read_input', side_effect=lambda _, name, **kw: Path(name).stem.encode())
            checked = patch(win.shell.fontconfig, '_inspect_project_catalog', side_effect=project_catalog)
            patch(win.shell.fontconfig, '_empty_catalog_cache')
            preflight = patch(win.shell, '_preflight_system_identity', return_value=((), images, recheck))
            from vtx.render import gfx_environment
            patch(gfx_environment, '_windows_boot', return_value='test boot')
            scope = win._WindowsRuntimeScope(ctx, root, op, images, pins, False, ())
            a = scope.for_fonts(ctx, ('fonts/First.TTF',))
            b = a.for_fonts(ctx, ('fonts/Second.otf',))
            empty = b.for_fonts(ctx, ())
            self.assertIs(b.for_fonts(ctx, ('fonts/First.TTF',)), a)
            self.assertEqual({item.context[2] for item in (a, b, empty)},
                             {first_shell, bundle / 'shell-1', bundle / 'shell-2'})
            self.assertTrue(all(item.context[0] is op for item in (a, b, empty)))
            self.assertEqual(a.font_faces(ctx, 'fonts/First.TTF', svg.digest(b'First'), None),
                             [{'family': 'First', 'style': 'Regular', 'index': 0}])
            with self.assertRaisesRegex(ValueError, 'changed after staging'):
                a.font_faces(ctx, 'fonts/First.TTF', svg.digest(b'Second'), None)
            self.assertEqual(empty.fontconfig_identity()['mode'], 'explicitly-font-free')
            self.assertEqual(a.fontconfig_identity()['mode'], 'project-explicit')
            self.assertEqual(checked.call_count, 2)
            preflight.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'scope is unavailable'):
            a.for_fonts(ctx, ('fonts/First.TTF',))

    def test_cached_frame_requires_clip_receipt_and_reads_only_requested_frame(self):
        root = temporary_root(self)
        engine = {'name': 'test'}
        runtime = win._WindowsRasterRuntime(root, None, engine, None, None)
        clip, other = {'dst_in': 3, 'dur': 2}, {'dst_in': 9, 'dur': 2}
        record = {'engine': engine, 'fonts': [], 'frame_count': 2, 'width': 2, 'height': 2}
        key = svg.record_key(record)
        data = png(2, 2)
        marker = svg.canonical({'format': 'framivra-svg-completion/v1', 'key': key, 'input': record,
                                'sha256': ['0' * 64, svg.digest(data)]})
        ctx = SimpleNamespace(root=root, verify_inputs=mock.Mock(),
                              _svg_receipt_records={svg.canonical(clip): svg.digest(svg.canonical(record))})
        page = mock.Mock()
        page.read.side_effect = {svg.COMPLETE_FILE: marker, 'f0001.png': data}.get
        with mock.patch.object(runtime, '_locked_page', return_value=nullcontext(page)) as open_page, \
                mock.patch.object(runtime, 'verify') as verify:
            with self.assertRaisesRegex(ValueError, 'pinned receipt input'):
                runtime.cached_frame(ctx, other, record, 1)
            with self.assertRaisesRegex(ValueError, 'outside the clip'):
                runtime.cached_frame(ctx, clip, record, -1)
            open_page.assert_not_called()
            self.assertEqual(runtime.cached_frame(ctx, clip, record, 1), data)
            open_page.assert_called_once_with(key, create=False)
            self.assertEqual([call.args[0] for call in page.read.call_args_list], [svg.COMPLETE_FILE, 'f0001.png'])
            verify.assert_called_once()
        ctx.verify_inputs.assert_called_once_with(None)

    def test_repeat_exports_use_distinct_private_paths_and_clip_local_outputs(self):
        cwd = temporary_root(self)
        shell = win.shell
        with ExitStack() as pins, \
                mock.patch.object(shell, '_pin_dir', side_effect=lambda path: nullcontext(path)), \
                mock.patch.object(shell, '_pin_entry', side_effect=lambda path: nullcontext((path, None, None, False))), \
                mock.patch.object(shell, '_read_pinned', side_effect=lambda path, *_: path.read_bytes()), \
                mock.patch.object(shell, '_require_runtime_facts'), mock.patch.object(shell, '_run'), \
                mock.patch.object(shell, '_fixed_stdin', side_effect=lambda *args, **kw: nullcontext(None)) as plans:
            context = (None, None, cwd, None, pins)
            result = [shell._export_bound_sources(context, [(3, data)], 2, 2,
                                                  deadline=shell.time.monotonic() + 5, capture_limit=1000,
                                                  export_id=export_id)
                      for export_id, data in (('a' * 16, b'first'), ('b' * 16, b'second'))]
        for (_, directory, names), export_id, data, command in zip(result, ('a' * 16, 'b' * 16),
                                                                    (b'first', b'second'), plans.call_args_list):
            self.assertEqual(directory.parent, cwd)
            self.assertEqual(names, ['f0003.png'])
            self.assertEqual((cwd / f'{export_id}-f0003.svg').read_bytes(), data)
            self.assertIn(f'export-filename:{directory.name}/f0003.png'.encode(), command.args[1])
            self.assertEqual(command.kwargs, {'export_id': export_id})
        self.assertNotEqual(result[0][1], result[1][1])

    def test_catalog_request_cannot_select_external_roots_or_leaves(self):
        root = Path('bundle')
        request = win.shell.fontconfig._project_catalog_request
        sha = 'a' * 64
        request(root, root / 'shell-2', (('font0.otf', sha), ('font1.ttc', sha)))
        for cwd, files in ((root / 'fonts', (('font0.otf', sha),)),
                           (root / 'shell', (('../font0.otf', sha),)),
                           (root / 'shell', (('font1.otf', sha),)),
                           (root / 'shell', (('font0.otf', 'unbound'),))):
            with self.subTest(cwd=cwd, files=files), self.assertRaisesRegex(ValueError, 'private staging'):
                request(root, cwd, files)


if __name__ == '__main__':
    unittest.main()
