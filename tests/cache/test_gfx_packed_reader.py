"""Packed proof-consumer tests with synthetic PNG/receipts, never real rendering."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

from tests.cache.test_gfx_pack import png, digest
from tests.gfx._environment import bind_environment
from vtx.cache import hygiene
from vtx.cache.cache_state import InputIdentityError
from vtx.cache.policy import CachePolicy
from vtx.cache.session import pack_path
from vtx.pipeline.infra import Ctx
from vtx.render import (gfx_backend, gfx_cli, gfx_electron, gfx_electron_stage,
                        gfx_environment, gfx_pack, gfx_packed_reader, gfx_svg)
from vtx.runtime import fstxn


@unittest.skipUnless(sys.platform == 'linux' and fstxn.write_capability() == 'fd-anchored',
                     'requires Linux safe packed storage')
class PackedReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS='')
        self.env.start()
        self.addCleanup(self.env.stop)
        bind_environment(self)
        (self.root / 'page.html').write_text('<html><body>fixture</body></html>')
        self.timeline = self.root / 'timeline.json'
        self.tl = {'version': 2, 'project': {'fps': 30, 'width': 32, 'height': 32, 'duration_frames': 3},
                   'sources': [], 'subtitles': [], 'audio_tracks': [], 'video_tracks':
                   [{'kind': 'gfx', 'clips': [{'src': 'page.html', 'dur': 3, 'dst_in': 0}]}]}
        self.frames = [png(i) for i in range(3)]

    def context(self):
        self.timeline.write_text(json.dumps(self.tl))
        self.c = Ctx(self.timeline)
        self.c.cache_policy = CachePolicy('core')
        self.clip = self.c.tl['video_tracks'][0]['clips'][0]
        self.directory = hygiene.gfx_dir(self.c, self.clip)
        self.directory.mkdir(parents=True, exist_ok=True)
        for i, data in enumerate(self.frames):
            (self.directory / f'f{i:04d}.png').write_bytes(data)

    def html(self, *, strict=False):
        if strict:
            self.tl['project']['gfx_dependency_policy'] = 'strict'
        self.context()
        options = {}
        if strict:
            from vtx.render.gfx_dependencies import resolve, digest as dependency_digest
            options = dict(dependency_digest=dependency_digest(resolve(self.c, self.clip)),
                           dependency_enforcement='fetch-pinned-local-v1',
                           dependency_observed=[(self.root / 'page.html').as_uri()])
        gfx_cli.complete_page(self.directory, 3, self.directory.name, 32, 32,
                             verify_inputs=lambda: self.c.verify_inputs('gfx'),
                             environment=gfx_environment.certificate(self.c), **options)

    def store(self, *, delete=True):
        self.record = json.loads((self.directory / 'complete.json').read_bytes())
        self.archive = pack_path(self.c, self.clip)
        self.archive.parent.mkdir(parents=True, exist_ok=True)
        gfx_pack.pack_page(self.directory, self.archive, expected_record=self.record, root=self.root)
        if delete:
            with gfx_pack.PagePack(self.archive, expected_record=self.record) as page:
                names = page.metadata_names
            for i in range(3):
                (self.directory / f'f{i:04d}.png').unlink()
            for name in names:
                (self.directory / name).unlink()

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes() if p.is_file() else None)
                for p in self.root.rglob('*')}

    def test_html_reads_original_frame_readonly_through_shared_proof_gate(self):
        self.html()
        self.store()
        before = self.snapshot()
        with mock.patch.object(gfx_cli, 'verify_page_record', wraps=gfx_cli.verify_page_record) as proof, \
                mock.patch.object(gfx_cli, 'render_cli', side_effect=AssertionError('no rendering')):
            self.assertEqual(gfx_packed_reader.read_frame(self.c, self.clip, 2), self.frames[2])
        proof.assert_called_once()
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.directory / 'complete.json').exists())

    def test_off_refuses_before_pack_access_or_input_checks(self):
        self.html()
        self.store()
        self.c.cache_policy = CachePolicy('off')
        with mock.patch.object(gfx_packed_reader, 'inspect_pack') as inspect, \
                mock.patch.object(gfx_packed_reader, 'verify_input_identity') as inputs:
            with self.assertRaisesRegex(ValueError, 'disabled'):
                gfx_packed_reader.read_frame(self.c, self.clip, 0)
        inspect.assert_not_called()
        inputs.assert_not_called()

    def test_unknown_or_mismatched_environment_cannot_become_eligible(self):
        self.html()
        self.store()
        changed = dict(self.record['environment'], sha256='f' * 64)
        with mock.patch.object(gfx_environment, 'certificate', return_value=changed):
            with self.assertRaisesRegex(ValueError, 'certificate changed'):
                gfx_packed_reader.read_frame(self.c, self.clip, 0)
        with mock.patch.object(gfx_environment, 'certificate', side_effect=EnvironmentError('unknown')):
            with self.assertRaisesRegex(EnvironmentError, 'unknown'):
                gfx_packed_reader.read_frame(self.c, self.clip, 0)

    def test_strict_uses_current_dependency_proof_and_rejects_missing_enforcement(self):
        self.html(strict=True)
        self.store(delete=False)
        self.assertEqual(gfx_packed_reader.read_frame(self.c, self.clip, 1), self.frames[1])
        self.record.pop('dependency_enforcement')
        (self.directory / 'complete.json').write_text(json.dumps(self.record))
        self.store()
        with self.assertRaisesRegex(ValueError, 'certificate changed'):
            gfx_packed_reader.read_frame(self.c, self.clip, 1)

    def test_changed_inputs_during_byte_read_and_corrupt_storage_fail_closed(self):
        self.html()
        self.store()
        original = gfx_pack.PagePack.read_frame
        def change(page, index):
            result = original(page, index)
            (self.root / 'page.html').write_text('<html><body>changed input</body></html>')
            return result
        before = self.snapshot()
        with mock.patch.object(gfx_pack.PagePack, 'read_frame', change), self.assertRaises(InputIdentityError):
            gfx_packed_reader.read_frame(self.c, self.clip, 0)
        after = self.snapshot()
        before.pop('page.html')
        after.pop('page.html')
        self.assertEqual(before, after, 'even failed preview must not write formal receipt')
        (self.root / 'page.html').write_text('<html><body>fixture</body></html>')
        data = self.archive.read_bytes()
        self.archive.write_bytes(data[:12] + bytes([data[12] ^ 1]) + data[13:])
        with self.assertRaises(gfx_pack.GfxPackError):
            gfx_packed_reader.read_frame(self.c, self.clip, 0)

    def test_svg_pure_completion_gate_is_shared_and_current_inputs_rechecked(self):
        inputs = {'frame_count': 3, 'width': 32, 'height': 32, 'fps': 30, 'engine': 'fixture-only'}
        clip = {'src': 'fixture-sequence.json', 'dur': 3, 'format': 'svg-sequence'}
        c = SimpleNamespace(root=self.root, cache=self.root / 'cache', w=32, h=32, fps=30,
                            tl={'video_tracks': [{'kind': 'gfx', 'clips': [clip]}]},
                            cache_policy=CachePolicy('core'), verify_inputs=mock.Mock())
        with mock.patch.object(gfx_svg, 'resolve', return_value=inputs), \
                mock.patch.object(gfx_packed_reader, 'verify_input_identity') as input_checks:
            self.c, self.clip = c, clip
            self.directory = hygiene.gfx_dir(c, clip)
            self.directory.mkdir(parents=True)
            for i, data in enumerate(self.frames):
                (self.directory / f'f{i:04d}.png').write_bytes(data)
            record = {'format': 'framivra-svg-completion/v1', 'key': self.directory.name,
                      'input': inputs, 'sha256': [digest(f) for f in self.frames]}
            (self.directory / 'complete.json').write_text(json.dumps(record))
            self.store()
            before = self.snapshot()
            with mock.patch.object(gfx_svg, 'verify_completion', wraps=gfx_svg.verify_completion) as proof:
                self.assertEqual(gfx_packed_reader.read_frame(c, clip, 0), self.frames[0])
            proof.assert_called_once()
            self.assertEqual(input_checks.call_args_list, [mock.call(c), mock.call(c)])
            self.assertEqual(before, self.snapshot())
            changed = dict(inputs, engine='changed')
            with mock.patch.object(gfx_svg, 'resolve', return_value=changed):
                with self.assertRaises((OSError, ValueError)):
                    gfx_packed_reader.read_frame(c, clip, 0)

    def test_missing_pack_uses_certified_bare_png_readonly(self):
        self.html()
        self.c.cache_policy = CachePolicy('aggressive')
        before = self.snapshot()
        self.assertEqual(gfx_packed_reader.read_frame(self.c, self.clip, 1), self.frames[1])
        self.assertEqual(before, self.snapshot())
        (self.directory / 'complete.json').unlink()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            gfx_packed_reader.read_frame(self.c, self.clip, 1)
        self.assertEqual(before, self.snapshot(), 'legacy bare frames never gain a certificate')

    def test_present_corrupt_pack_never_falls_back_to_valid_bare_png(self):
        self.html()
        self.store(delete=False)
        self.archive.write_bytes(b'corrupt archive')
        with mock.patch('vtx.render.frame_source.png_source') as source:
            with self.assertRaises(gfx_pack.GfxPackError):
                gfx_packed_reader.read_frame(self.c, self.clip, 0)
        source.assert_not_called()
        self.archive.unlink()
        self.archive.symlink_to(self.root / 'missing-pack')
        with mock.patch('vtx.render.frame_source.png_source') as source:
            with self.assertRaises(gfx_pack.GfxPackError):
                gfx_packed_reader.read_frame(self.c, self.clip, 0)
        source.assert_not_called()

    def test_linked_pack_ancestor_is_not_treated_as_missing_archive(self):
        self.html()
        outside = self.root / 'outside-packs'
        outside.mkdir()
        linked = self.root / 'linked-packs'
        linked.symlink_to(outside, target_is_directory=True)
        with mock.patch.object(gfx_packed_reader, 'pack_path', return_value=linked / 'missing.vtxp'), \
                mock.patch('vtx.render.frame_source.png_source') as source:
            with self.assertRaises(ValueError):
                gfx_packed_reader.read_frame(self.c, self.clip, 0)
        source.assert_not_called()

    def test_dry_context_cannot_skip_real_input_identity_gate(self):
        self.html()
        self.store()
        self.c.dry = True
        (self.root / 'page.html').write_text('<html><body>changed</body></html>')
        with self.assertRaises(InputIdentityError):
            gfx_packed_reader.read_frame(self.c, self.clip, 0)

    def electron(self):
        binary = self.root / 'fake-electron'
        binary.write_bytes(b'unit-fixture-not-an-executable-image')
        binary.chmod(0o700)
        patch = mock.patch.dict(os.environ, VTX_GFX='electron', VTX_ELECTRON_BIN=str(binary),
                                VTX_ELECTRON_MODE='b0', VTX_ELECTRON_OUTPUT=gfx_backend.OUTPUT,
                                VTX_ELECTRON_TIMEOUT='15')
        patch.start()
        self.addCleanup(patch.stop)
        self.context()
        run_id, frames = uuid.uuid4().hex, list(range(3))
        evidence = self.directory / '.electron' / run_id
        evidence.mkdir(parents=True)
        raw = dict(schema='vtx.electron.navigation/1', run_id=run_id, mode='N', raster_mode='b0',
                   output=gfx_backend.OUTPUT, requested_backend='electron', effective_backend='electron',
                   failed=[], errors={}, max_inflight=1, shared_memory=False,
                   page_error_gate=gfx_electron_stage.PAGE_ERROR_GATE, page_errors={str(f): [] for f in frames},
                   viewport={'width': 32, 'height': 32, 'device_scale_factor': 1}, frames=[])
        for f, data in enumerate(self.frames):
            raw['frames'].append(dict(frame=f, path=gfx_backend.OUTPUT,
                url=gfx_electron.frame_url(self.root / 'page.html', f),
                output=str(self.directory / f'f{f:04d}.png'), page_error_fence_passed=True,
                baseline_veto_passed=True, png_semantics_passed=True, sha256=digest(data)))
        adapter = dict(run_id=run_id, evidence_dir=str(evidence), mode='N', raster_mode='b0',
                       rendered=3, exit_code=0, requested_backend='electron', effective_backend='electron',
                       failed=[], errors={}, path_counts={gfx_backend.OUTPUT: 3, 'failed': 0},
                       page_error_gate=raw['page_error_gate'], page_errors=raw['page_errors'])
        request = gfx_electron.expected_request(self.root / 'page.html', self.directory, frames, 32, 32,
                    run_id, mode='b0', output=gfx_backend.OUTPUT, timeout_s=15)
        for name, value in [('request.json', request), ('result.json', raw), ('adapter-result.json', adapter)]:
            (evidence / name).write_text(json.dumps(value))
        config = gfx_backend.electron_config()
        run = gfx_electron_stage.require_result(adapter, frames, self.directory, 32, 32,
                                                expected_html=self.root / 'page.html', expected_config=config)
        gfx_cli.complete_page(self.directory, 3, self.directory.name, 32, 32,
            verify_inputs=lambda: self.c.verify_inputs('gfx'), backend_ns=hygiene.gfx_cache_ns(),
            backend_identity=gfx_backend.identity(), render_runs=[run])
        return evidence

    def test_electron_uses_same_full_producer_gate_without_reconstructing_paths(self):
        self.electron()
        self.store()
        before = self.snapshot()
        with mock.patch.object(gfx_electron_stage, 'require_result', wraps=gfx_electron_stage.require_result) as proof, \
                mock.patch.object(gfx_pack.PagePack, 'read_frame', autospec=True,
                                  side_effect=gfx_pack.PagePack.read_frame) as read:
            self.assertEqual(gfx_packed_reader.read_frame(self.c, self.clip, 1), self.frames[1])
        proof.assert_called_once()
        self.assertGreaterEqual(read.call_count, 7, 'Electron preserves full-page gates, not just requested frame')
        self.assertEqual(before, self.snapshot())

    def test_electron_untrusted_record_cannot_set_resource_bounds(self):
        self.electron()
        self.store()
        hostile = copy.deepcopy(self.record)
        hostile['frames'] = 10 ** 100
        with mock.patch.object(gfx_packed_reader, 'inspect_pack', return_value={'record': hostile}), \
                mock.patch.object(gfx_packed_reader, 'PagePack') as page:
            with self.assertRaisesRegex(ValueError, 'current frame contract'):
                gfx_packed_reader.read_frame(self.c, self.clip, 0)
        page.assert_not_called()

    def test_electron_bad_sidecar_never_adopted_even_when_package_is_internally_intact(self):
        evidence = self.electron()
        result = evidence / 'result.json'
        raw = json.loads(result.read_bytes())
        raw['frames'][0]['output'] = '/different/canonical/f0000.png'
        result.write_text(json.dumps(raw))
        self.store()
        with self.assertRaisesRegex(ValueError, 'producer/completion'):
            gfx_packed_reader.read_frame(self.c, self.clip, 1)
        self.assertFalse((self.directory / 'complete.json').exists())

    def test_electron_missing_receipt_and_changed_backend_settings_refused(self):
        evidence = self.electron()
        result = evidence / 'result.json'
        raw = json.loads(result.read_bytes())
        raw['frames'].pop()
        result.write_text(json.dumps(raw))
        self.store()
        with self.assertRaisesRegex(ValueError, 'producer/completion'):
            gfx_packed_reader.read_frame(self.c, self.clip, 0)
        with mock.patch.dict(os.environ, VTX_ELECTRON_TIMEOUT='16'):
            with self.assertRaises((OSError, ValueError, InputIdentityError)):
                gfx_packed_reader.read_frame(self.c, self.clip, 0)


if __name__ == '__main__':
    unittest.main()
