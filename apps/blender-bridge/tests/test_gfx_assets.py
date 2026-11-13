"""Fast synthetic gfx delivery tests: no Blender, browser or media subprocess."""
import importlib.util
from pathlib import Path
import sys
from unittest import mock

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
sys.path.insert(0, str(REPO))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load('bridge_gfx_cache_fixture', APP / 'tests' / 'test_gfx_cache.py')
assets = load('bridge_gfx_assets', APP / 'gfx_assets.py')
from vtx.cache.cache_state import InputIdentityError, receipt_fingerprint
from vtx.cache.policy import CachePolicy
from vtx.cache.session import pack_path
from vtx.render import gfx_pack


class GfxDelivery(fixtures.CacheFixture):
    def setUp(self):
        super().setUp()
        self.c.tl_path = self.timeline
        self.c.cache_policy = CachePolicy('core')
        self.c.input_fingerprint = receipt_fingerprint(self.c)
        self.blend = self.root / 'preview.blend'
        self.no_process = mock.patch('subprocess.Popen', side_effect=AssertionError('no external producer'))
        self.no_process.start()
        self.addCleanup(self.no_process.stop)

    def packed(self):
        directory = self.seed()
        import json
        archive = pack_path(self.c, self.clip)
        archive.parent.mkdir(parents=True, exist_ok=True)
        record = json.loads((directory / 'complete.json').read_bytes())
        gfx_pack.pack_page(directory, archive, expected_record=record, root=self.root)
        for path in [directory / 'complete.json', *(directory / f'f{i:04d}.png' for i in range(3))]:
            path.unlink()
        return directory

    def canonical(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and 'preview.assets' not in p.parts}

    def test_native_platform_fallback_pins_before_and_after_without_delivery_writes(self):
        files = [self.root / 'cache' / 'gfx' / f'f{i:04d}.png' for i in range(3)]
        for platform, capability in [('darwin', 'fd-anchored'), ('linux', 'legacy')]:
            with self.subTest(platform=platform, capability=capability):
                events = []
                with mock.patch.object(assets, 'sys', mock.Mock(platform=platform)), \
                        mock.patch.object(assets.fstxn, 'write_capability', return_value=capability), \
                        mock.patch.object(assets, 'verify_input_identity', side_effect=lambda c: events.append('pin')) as pin, \
                        mock.patch('vtx.render.gfx_cache_reader.resolve_frames',
                                   side_effect=lambda c, clip: events.append('resolve') or files) as resolve, \
                        mock.patch.object(assets, 'read_frame', side_effect=AssertionError('no packed getter')) as read, \
                        mock.patch.object(assets.fstxn, 'mkdir', side_effect=AssertionError('no asset writes')) as mkdir:
                    self.assertEqual(assets.materialize_frames(self.c, self.clip, self.blend), files)
                self.assertEqual(events, ['pin', 'resolve', 'pin'])
                self.assertEqual(pin.call_count, 2)
                resolve.assert_called_once_with(self.c, self.clip)
                read.assert_not_called()
                mkdir.assert_not_called()
        self.assertFalse((self.root / 'preview.assets').exists())

    def test_core_packed_original_bytes_become_independent_delivery(self):
        directory = self.packed()
        before = self.canonical()
        files = assets.materialize_frames(self.c, self.clip, self.blend)
        self.assertEqual(files[0].parents[2], self.root / 'preview.assets')
        self.assertEqual([p.read_bytes() for p in files], [fixtures.png_bytes(i) for i in range(3)])
        self.assertEqual(assets.materialize_frames(self.c, self.clip, self.blend), files)
        self.assertFalse(list(directory.glob('f*.png')))
        self.assertFalse(list((self.root / 'preview.assets').rglob('complete.json')))
        self.assertEqual(self.canonical(), before)

    def test_aggressive_bare_is_copied_and_survives_switch_to_core(self):
        self.c.cache_policy = CachePolicy('aggressive')
        directory = self.seed()
        files = assets.materialize_frames(self.c, self.clip, self.blend)
        self.assertNotEqual(files[0].parent, directory)
        self.c.cache_policy = CachePolicy('core')
        self.packed()
        self.assertEqual([p.read_bytes() for p in files], [fixtures.png_bytes(i) for i in range(3)])
        self.assertEqual(assets.materialize_frames(self.c, self.clip, self.blend), files)

    def test_off_refuses_old_gfx_and_private_fresh_is_not_claimed_supported(self):
        self.seed()
        self.c.cache_policy = CachePolicy('off')
        with mock.patch.object(assets, 'read_frame') as read, mock.patch.object(assets, 'verify_input_identity') as pin:
            with self.assertRaisesRegex(ValueError, 'private fresh gfx'):
                assets.materialize_frames(self.c, self.clip, self.blend)
        read.assert_not_called()
        pin.assert_not_called()
        self.assertFalse((self.root / 'preview.assets').exists())

    def test_late_frame_failure_never_publishes_partial_sequence(self):
        self.seed()
        real = assets.read_frame
        def fail(c, clip, index):
            if index == 2:
                raise ValueError('incomplete page')
            return real(c, clip, index)
        before = self.canonical()
        with mock.patch.object(assets, 'read_frame', side_effect=fail), self.assertRaisesRegex(ValueError, 'incomplete'):
            assets.materialize_frames(self.c, self.clip, self.blend)
        self.assertFalse(list((self.root / 'preview.assets').rglob('f*.png')))
        self.assertFalse(list((self.root / 'preview.assets').rglob('*.tmp')))
        self.assertEqual(self.canonical(), before)

    def test_final_input_pin_rejects_change_after_last_frame(self):
        self.seed()
        real = assets.read_frame
        def change(c, clip, index):
            data = real(c, clip, index)
            if index == 2:
                (self.root / 'gfx' / 'page.html').write_text('changed after byte verification')
            return data
        with mock.patch.object(assets, 'read_frame', side_effect=change), self.assertRaises(InputIdentityError):
            assets.materialize_frames(self.c, self.clip, self.blend)
        self.assertFalse(list((self.root / 'preview.assets').rglob('f*.png')))
        self.assertFalse(list((self.root / 'preview.assets').rglob('*.tmp')))
        self.assertFalse((self.root / 'cache' / 'receipt.json').exists())

    def test_symlinked_delivery_cannot_redirect_writes_or_supply_assets(self):
        self.seed()
        protected = self.root / 'protected'
        protected.mkdir()
        for i in range(3):
            (protected / f'f{i:04d}.png').write_bytes(fixtures.png_bytes(i))
        directory = self.blend.with_suffix('.assets') / 'gfx' / assets.gfx_key(self.c, self.clip)
        directory.parent.mkdir(parents=True)
        directory.symlink_to(protected, target_is_directory=True)
        before = {p.name: p.read_bytes() for p in protected.iterdir()}
        with self.assertRaises((OSError, ValueError)):
            assets.materialize_frames(self.c, self.clip, self.blend)
        self.assertEqual({p.name: p.read_bytes() for p in protected.iterdir()}, before)
        self.assertTrue(directory.is_symlink())
        self.assertFalse(list(directory.parent.glob('*.tmp')))

    def test_existing_delivery_is_not_silently_replaced(self):
        self.seed()
        files = assets.materialize_frames(self.c, self.clip, self.blend)
        replacement = fixtures.png_bytes(90)
        files[1].write_bytes(replacement)
        with self.assertRaisesRegex(ValueError, 'choose a new'):
            assets.materialize_frames(self.c, self.clip, self.blend)
        self.assertEqual(files[1].read_bytes(), replacement)
        self.assertFalse(list((self.root / 'preview.assets').rglob('*.tmp')))
