"""Public Electron route/cache gates. Real tests require VTX_TEST_ELECTRON=1.
Fixtures never use voice-ime. Retain real outputs via VTX_ELECTRON_EVIDENCE_DIR.
"""
import contextlib
import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
import wave
from unittest import mock

from vtx import cli, gfx_backend, gfx_cli, gfx_electron_stage, hygiene
from vtx.cache_state import InputIdentityError, verify_input_identity
from vtx.composite import RenderPlan
from vtx.infra import Ctx
from tests.gfx._png import png


def project(root, n=3):
    (root / 'page.html').write_text('''<!doctype html><style>html,body{margin:0;background:rgb(80,120,160)}</style><body></body>
<script>let f=Number(new URLSearchParams(location.search).get('f'));
document.body.style.background=`rgb(${80+f},120,160)`;</script>''')
    tl = dict(version=2, project=dict(fps=30, width=192, height=96, duration_frames=n),
              sources=[], subtitles=[], audio_tracks=[], video_tracks=[dict(name='g', kind='gfx',
              clips=[dict(src='page.html', dst_in=0, dur=n)])])
    p = root / 'timeline.json'
    p.write_text(json.dumps(tl))
    return p


class IntegrationGates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='electron-integration-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / 'fake-electron'
        self.binary.write_bytes(b'unit fixture A')
        self.binary.chmod(0o755)
        self.env = mock.patch.dict(os.environ, VTX_GFX='electron', VTX_ELECTRON_BIN=str(self.binary),
                                   VTX_ELECTRON_MODE='b0', VTX_ELECTRON_OUTPUT=gfx_backend.OUTPUT,
                                   VTX_ELECTRON_TIMEOUT='15', VTX_GFX_CACHE_NS='', VTX_GFX_PAGES='2',
                                   VTX_GFX_SPLIT_FRAMES='1')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.p = project(self.root)
        self.c = Ctx(self.p)
        self.cl = self.c.tl['video_tracks'][0]['clips'][0]
        self.d = hygiene.gfx_dir(self.c, self.cl)
        self.calls = []

    def fake_render(self, html, d, n, **kw):
        frames = kw['frames']
        self.calls.append(frames)
        self.assertFalse((d / gfx_cli.COMPLETE_FILE).exists())
        run_id = uuid.uuid4().hex
        evidence = d / '.electron' / run_id
        evidence.mkdir(parents=True)
        receipts = []
        for f in frames:
            target = d / f'f{f:04d}.png'
            target.write_bytes(png(192, 96, value=f))
            receipts.append(dict(frame=f, path=gfx_backend.OUTPUT,
                                 url=Path(html).resolve().as_uri() + f'?f={f}', output=str(target.resolve()),
                                 page_error_fence_passed=True, baseline_veto_passed=True,
                                 png_semantics_passed=True, sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
        raw = dict(schema='vtx.electron.navigation/1', run_id=run_id, mode='N', raster_mode='b0',
                   output=gfx_backend.OUTPUT, requested_backend='electron', effective_backend='electron',
                   failed=[], errors={}, frames=receipts, max_inflight=1, shared_memory=False,
                   page_error_gate=gfx_electron_stage.PAGE_ERROR_GATE, page_errors={str(f): [] for f in frames},
                   viewport=dict(width=192, height=96, device_scale_factor=1))
        st = dict(run_id=run_id, evidence_dir=str(evidence), mode='N', raster_mode='b0',
                  rendered=len(frames), exit_code=0, requested_backend='electron', effective_backend='electron',
                  failed=[], errors={}, path_counts={gfx_backend.OUTPUT: len(frames), 'failed': 0},
                  page_error_gate=raw['page_error_gate'], page_errors=raw['page_errors'])
        job = dict(run_id=run_id, page=str(Path(html).resolve()), out_dir=str(d.resolve()),
                   result=str(evidence.resolve() / 'result.json'), width=192, height=96, frames=frames,
                   timeout_ms=int(kw['timeout_s'] * 1000), mode=kw['mode'], output=kw['output'],
                   source_sha256=hashlib.sha256(Path(html).read_bytes()).hexdigest())
        (evidence / 'request.json').write_text(json.dumps(job))
        (evidence / 'result.json').write_text(json.dumps(raw))
        (evidence / 'adapter-result.json').write_text(json.dumps(st))
        return st

    def run_gfx(self, renderer=None):
        with mock.patch.object(gfx_electron_stage, 'render_page', side_effect=renderer or self.fake_render), \
                mock.patch.object(gfx_cli, 'render_cli', side_effect=AssertionError('forbidden CLI fallback')), \
                mock.patch('vtx.gfx_render.render_page', side_effect=AssertionError('forbidden CDP fallback')), \
                mock.patch.object(cli, 'find_chrome', side_effect=AssertionError('forbidden Chrome lookup')), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.gfx(self.c)

    def test_selector_unknown_is_rejected_and_cef_retained(self):
        for value in ('typo', '', 'ElectronGPU'):
            with self.subTest(value=value), mock.patch.dict(os.environ, VTX_GFX=value), self.assertRaises(ValueError):
                Ctx(self.p)
        for value in ('cdp', 'cli', 'cef', 'electron', ' Electron '):
            with mock.patch.dict(os.environ, VTX_GFX=value):
                self.assertEqual(gfx_backend.selected(), value.strip().lower())

    def test_missing_binary_and_unsupported_modes_fail_even_before_cache(self):
        for env in ({'VTX_ELECTRON_BIN': str(self.root / 'missing')}, {'VTX_ELECTRON_MODE': 'b1'},
                    {'VTX_ELECTRON_OUTPUT': 'bitmap_shared_encoder'}, {'VTX_ELECTRON_TIMEOUT': 'nan'}):
            with self.subTest(env=env), mock.patch.dict(os.environ, **env), self.assertRaises(ValueError):
                Ctx(self.p)

    def test_explicit_cli_never_calls_electron_or_cdp(self):
        with mock.patch.dict(os.environ, VTX_GFX='cli'), \
                mock.patch.object(gfx_electron_stage, 'render_page', side_effect=AssertionError('Electron')), \
                mock.patch('vtx.gfx_render.render_page', side_effect=AssertionError('CDP')), \
                mock.patch.object(cli, 'find_chrome', return_value='unit-cli'):
            c = Ctx(self.p)
            def render(frames, d, html, chrome, **kw):
                self.assertEqual(chrome, 'unit-cli')
                for f in frames: (d / f'f{f:04d}.png').write_bytes(png(192, 96, f))
            with mock.patch.object(gfx_cli, 'render_cli', side_effect=render): cli.gfx(c)
            self.assertTrue((hygiene.gfx_dir(c, self.cl) / gfx_cli.COMPLETE_FILE).exists())

    def test_split_complete_warm_hit_and_receipt_identity(self):
        self.run_gfx()
        self.assertEqual(sorted(f for fs in self.calls for f in fs), [0, 1, 2])
        marker = json.loads((self.d / gfx_cli.COMPLETE_FILE).read_text())
        self.assertEqual(marker['backend_identity'], gfx_backend.identity())
        self.assertEqual(len(marker['render_runs']), 3)
        self.assertEqual(marker['backend_ns'], 'electron_b0')
        self.run_gfx(renderer=lambda *a, **k: self.fail('warm cache launched renderer'))
        rec = json.loads((self.c.out / 'receipt.json').read_text())
        self.assertEqual(rec['stages']['gfx']['effective_backend'], 'electron')
        self.assertEqual(rec['gfx_backend'], rec['stages']['gfx']['gfx_backend'])
        self.assertEqual(len(rec['stages']['gfx']['cached_page_keys']), 1)

    def test_blender_reader_uses_exact_completed_electron_cache(self):
        from vtx.gfx_cache_reader import resolve_frames, GfxCacheError
        self.run_gfx()
        expected = [self.d / f'f{f:04d}.png' for f in range(3)]
        self.assertEqual(resolve_frames(self.c, self.cl), expected)
        marker = self.d / gfx_cli.COMPLETE_FILE
        original = json.loads(marker.read_text())
        for change in ({'backend_identity': 'stale'}, {'render_runs': []},
                       {'backend_ns': 'other'}, {'key': 'stale'}, {'sha256': []}):
            with self.subTest(change=change):
                marker.write_text(json.dumps(dict(original, **change)))
                before = marker.read_bytes()
                with self.assertRaises(GfxCacheError): resolve_frames(self.c, self.cl)
                self.assertEqual(marker.read_bytes(), before)
        marker.write_text(json.dumps(original))
        with mock.patch.dict(os.environ, VTX_GFX_CACHE_NS='other'):
            with self.assertRaises(GfxCacheError): resolve_frames(self.c, self.cl)
        self.binary.write_bytes(b'changed executable')
        with self.assertRaises(GfxCacheError): resolve_frames(self.c, self.cl)

    def test_reader_rejects_missing_or_corrupt_actual_producer_receipts(self):
        from vtx.gfx_cache_reader import resolve_frames, GfxCacheError
        self.run_gfx()
        record = json.loads((self.d / gfx_cli.COMPLETE_FILE).read_text())
        run = record['render_runs'][0]
        for name in ('adapter-result.json', 'result.json'):
            p = self.d / '.electron' / run['run_id'] / name
            original = p.read_bytes()
            for content in (b'{}', b'{invalid'):
                p.write_bytes(content)
                with self.assertRaises(GfxCacheError): resolve_frames(self.c, self.cl)
            p.write_bytes(original)
        self.assertEqual(len(resolve_frames(self.c, self.cl)), 3)

    def test_current_caller_rejects_forged_provenance_readonly_and_producer(self):
        from vtx.gfx_cache_reader import resolve_frames, GfxCacheError
        cases = [
            ('result.json', lambda o: o['frames'][0].update(url='file:///unrelated-source.html?f=0')),
            ('result.json', lambda o: o['frames'][0].update(output='/unrelated/f0000.png')),
            ('request.json', lambda o: o.update(page='/unrelated-source.html')),
            ('request.json', lambda o: o.update(mode='b1', timeout_ms=500)),
        ]
        self.run_gfx()
        record = json.loads((self.d / gfx_cli.COMPLETE_FILE).read_text())
        ev = self.d / '.electron' / record['render_runs'][0]['run_id']
        for name, change in cases:
            with self.subTest(phase='readonly', name=name, change=change):
                p = ev / name; original = p.read_bytes()
                obj = json.loads(original); change(obj); p.write_text(json.dumps(obj))
                try:
                    with self.assertRaises(GfxCacheError): resolve_frames(self.c, self.cl)
                finally: p.write_bytes(original)
        for name, change in cases:
            with self.subTest(phase='producer', name=name, change=change):
                (self.d / gfx_cli.COMPLETE_FILE).unlink(missing_ok=True)
                def broken(html, d, n, **kw):
                    st = self.fake_render(html, d, n, **kw)
                    p = Path(st['evidence_dir']) / name
                    obj = json.loads(p.read_text()); change(obj); p.write_text(json.dumps(obj))
                    return st
                with self.assertRaises(SystemExit): self.run_gfx(broken)
                self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())

    def test_combined_external_binding_and_missing_context_fail_closed(self):
        from vtx.gfx_cache_reader import resolve_frames, GfxCacheError
        from vtx.gfx_electron import ElectronRenderError
        self.run_gfx()
        marker = json.loads((self.d / gfx_cli.COMPLETE_FILE).read_text())
        run = marker['render_runs'][0]; ev = self.d / '.electron' / run['run_id']
        paths = [ev / n for n in ('request.json', 'result.json', 'adapter-result.json')]
        original = [p.read_bytes() for p in paths]
        request, raw, adapter = [json.loads(b) for b in original]
        # Receipts agree with each other but NOT the real current source/settings.
        request.update(page='/unrelated-source.html', mode='b1', timeout_ms=500)
        raw['frames'][0]['url'] = 'file:///unrelated-source.html?f=0'
        raw['raster_mode'] = adapter['raster_mode'] = 'b1'
        for p, obj in zip(paths, (request, raw, adapter)): p.write_text(json.dumps(obj))
        try:
            with self.assertRaises(GfxCacheError): resolve_frames(self.c, self.cl)
        finally:
            for p, b in zip(paths, original): p.write_bytes(b)
        adapter = json.loads(original[2])
        with self.assertRaises(ElectronRenderError):
            gfx_electron_stage.require_result(adapter, run['frames'], self.d, 192, 96)
        self.assertIsNone(gfx_electron_stage.cached_runs(self.d, 3, self.d.name, 192, 96,
                                                       gfx_backend.identity(), hygiene.gfx_cache_ns()))
        # Valid bytes behind a symlink are still not producer evidence.
        for p in [*paths, self.d / 'f0000.png', self.d / gfx_cli.COMPLETE_FILE]:
            with self.subTest(symlink=p.name):
                b = p.read_bytes(); target = self.root / ('symlink-target-' + p.name)
                target.write_bytes(b)
                self.assertTrue(p.resolve().is_relative_to(self.root.resolve()))
                p.unlink(); p.symlink_to(target)
                try:
                    with self.assertRaises(GfxCacheError): resolve_frames(self.c, self.cl)
                finally:
                    self.assertTrue(p.is_symlink()); p.unlink(); p.write_bytes(b)

    def test_strict_electron_rejected_before_launch_or_publication(self):
        self.c.tl['project']['gfx_dependency_policy'] = 'strict'
        with mock.patch.object(gfx_electron_stage, 'render_stage') as launch:
            with self.assertRaisesRegex(ValueError, 'strict dependencies require CDP'):
                cli._gfx(self.c)
            launch.assert_not_called()
        from vtx.gfx_cache_reader import resolve_frames, GfxCacheError
        with self.assertRaisesRegex(GfxCacheError, 'strict dependencies require CDP'):
            resolve_frames(self.c, self.cl)
        self.assertFalse(self.d.exists())

    def test_electron_events_use_actual_backend_and_reader_accepts_it(self):
        from vtx import events
        from tests.execution.test_execution_events import Buffer
        stream = Buffer()
        self.c.execution = events.ExecutionContext(stream)
        self.run_gfx()
        self.run_gfx(renderer=lambda *a, **kw: self.fail('warm cache launched renderer'))
        self.c.execution.finish(0)
        result = events.read_events(stream.getvalue())
        self.assertTrue(result['complete'])
        rows = [r for r in result['events'] if r['event'] == 'attempt_start'
                and r['extensions'].get('backend')]
        self.assertTrue(rows)
        self.assertEqual({r['extensions']['backend'] for r in rows}, {'electron'})
        hits = [r for r in result['events'] if r['event'] == 'cache_hit']
        self.assertEqual([r['extensions']['validator'] for r in hits], ['cached_runs'])
        forged = stream.getvalue().replace('"backend":"electron"', '"backend":"unknown"')
        with self.assertRaises(ValueError): events.read_events(forged)

    def test_failed_or_incomplete_new_receipt_cannot_use_old_png(self):
        for kind in ('failed', 'missing-run', 'missing-frame', 'wrong-backend', 'exception'):
            with self.subTest(kind=kind):
                self.d.mkdir(parents=True, exist_ok=True)
                for f in range(3): (self.d / f'f{f:04d}.png').write_bytes(png(192, 96, 42))
                def broken(html, d, n, **kw):
                    if kind == 'exception': raise RuntimeError('injected Electron start failure')
                    st = self.fake_render(html, d, n, **kw)
                    if kind == 'failed': st['failed'] = kw['frames']
                    if kind == 'missing-run': st.pop('run_id')
                    if kind == 'wrong-backend': st['effective_backend'] = 'cdp'
                    if kind == 'missing-frame':
                        p = Path(st['evidence_dir']) / 'result.json'
                        raw = json.loads(p.read_text()); raw['frames'] = []; p.write_text(json.dumps(raw))
                    return st
                with self.assertRaises(SystemExit): self.run_gfx(broken)
                self.assertFalse((self.d / gfx_cli.COMPLETE_FILE).exists())
                self.assertFalse(json.loads((self.c.out / 'receipt.json').read_text())['stages']['gfx']['ok'])
                self.assertTrue(all((self.d / f'f{f:04d}.png').exists() for f in range(3)))

    def test_unmarked_or_damaged_completion_requires_fresh_all_frames(self):
        self.run_gfx()
        marker = self.d / gfx_cli.COMPLETE_FILE
        for missing in ('marker', 'runs', 'hashes', 'identity'):
            with self.subTest(missing=missing):
                if missing == 'marker': marker.unlink()
                else:
                    obj = json.loads(marker.read_text())
                    obj.pop({'runs': 'render_runs', 'hashes': 'sha256', 'identity': 'backend_identity'}[missing])
                    marker.write_text(json.dumps(obj))
                self.calls.clear(); self.run_gfx()
                self.assertEqual(sorted(f for fs in self.calls for f in fs), [0, 1, 2])

    def test_binary_gate_namespace_change_keys_receipts_vseg_and_passes(self):
        def keys():
            c = Ctx(self.p); c.dry = True; c.gpu = False
            d = hygiene.gfx_dir(c, self.cl)
            from vtx.frame_source import png_source
            layer = dict(gfx=True, pngdir=d, source=png_source(c, self.cl),
                         cl=self.cl, x=0, y=0, w2=None, h2=None)
            plan = RenderPlan(c, False, 1, c.w, c.h)
            commands = []
            c.run = commands.append
            with mock.patch('vtx.composite.pick_vcodec', return_value=(['-c:v','libx264'], 'x264')):
                plan._composite_passes([layer, layer], c.out / 'seg.mp4', False, 0, 3)
            return (d, c.input_fingerprint, plan._seg_content_key(0, 3, [layer], False, None), commands[0][-1])
        a = keys()
        with mock.patch.dict(os.environ, VTX_GFX='cdp'): b = keys()
        with mock.patch.dict(os.environ, VTX_GFX_CACHE_NS='another_ns'): n = keys()
        self.binary.write_bytes(b'unit fixture B')
        d = keys()
        original = gfx_backend.content_digest
        implementations = []
        for name in ('page_error_gate.mjs', 'navigation_worker.mjs', 'pixel_codec.mjs'):
            with mock.patch.object(gfx_backend, 'content_digest', side_effect=lambda p:
                                   'changed-implementation' if Path(p).name == name else original(p)):
                implementations.append(keys())
        with mock.patch.dict(os.environ, VTX_ELECTRON_TIMEOUT='16'):
            settings = keys()
        for newer in (b, n, d, settings, *implementations):
            for old, new in zip(a, newer): self.assertNotEqual(old, new)
        with self.assertRaises(InputIdentityError): verify_input_identity(self.c)
        with mock.patch.dict(os.environ, VTX_GFX='cdp'):
            new = Ctx(self.p)
            with self.assertRaises(RuntimeError): new.begin_stage('video')


@unittest.skipUnless(os.environ.get('VTX_TEST_ELECTRON') == '1', 'real Electron opt-in')
class RealPublicIntegration(unittest.TestCase):
    def setUp(self):
        evidence = os.environ.get('VTX_ELECTRON_EVIDENCE_DIR')
        self.temp = tempfile.TemporaryDirectory(prefix='electron-public-') if not evidence else None
        self.addCleanup(lambda: self.temp.cleanup() if self.temp else None)
        self.root = Path(evidence or self.temp.name) / ('public-' + uuid.uuid4().hex[:12])
        self.root.mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, VTX_GFX='electron', VTX_ELECTRON_MODE='b0',
                                   VTX_ELECTRON_OUTPUT=gfx_backend.OUTPUT, VTX_GFX_CACHE_NS='integration_b0',
                                   VTX_GFX_SPLIT_FRAMES='1', VTX_GFX_PAGES='2', VTX_VIDEO_SEGMENTS='2',
                                   VTX_SEG_NVENC='0', VTX_IO_WORKERS='1')
        self.env.start(); self.addCleanup(self.env.stop)

    def test_real_route_split_failure_old_png_and_cache_switch(self):
        p = project(self.root)
        c = Ctx(p); cl = c.tl['video_tracks'][0]['clips'][0]; d = hygiene.gfx_dir(c, cl)
        with mock.patch.object(gfx_cli, 'render_cli', side_effect=AssertionError('CLI fallback')), \
                mock.patch('vtx.gfx_render.render_page', side_effect=AssertionError('CDP fallback')):
            cli.gfx(c)
            marker = json.loads((d / gfx_cli.COMPLETE_FILE).read_text())
            self.assertEqual(len(marker['render_runs']), 3)
            with mock.patch.object(gfx_electron_stage, 'render_page', side_effect=AssertionError('warm launch')):
                cli.gfx(Ctx(p))
            with mock.patch.dict(os.environ, VTX_GFX_CACHE_NS='integration_other'):
                switched = Ctx(p); switched_dir = hygiene.gfx_dir(switched, cl)
                self.assertNotEqual(switched_dir, d)
                cli.gfx(switched)
                new_runs = json.loads((switched_dir / gfx_cli.COMPLETE_FILE).read_text())['render_runs']
                self.assertFalse({r['run_id'] for r in marker['render_runs']} & {r['run_id'] for r in new_runs})
            # HTML changes select a NEW directory; seed legal old PNGs there.
            (self.root / 'page.html').write_text('<script>throw Error("integration-error")</script>')
            bad = Ctx(p); bad_dir = hygiene.gfx_dir(bad, cl); bad_dir.mkdir(parents=True)
            for f in range(3): (bad_dir / f'f{f:04d}.png').write_bytes((d / f'f{f:04d}.png').read_bytes())
            before = (bad_dir / 'f0000.png').read_bytes()
            with self.assertRaises(SystemExit): cli.gfx(bad)
            self.assertEqual((bad_dir / 'f0000.png').read_bytes(), before)
            self.assertFalse((bad_dir / gfx_cli.COMPLETE_FILE).exists())
            with mock.patch.dict(os.environ, VTX_ELECTRON_BIN=str(self.root / 'missing')):
                with self.assertRaises(ValueError): Ctx(p)
            with mock.patch.dict(os.environ, VTX_GFX='unknown'):
                with self.assertRaises(ValueError): Ctx(p)
            with mock.patch.dict(os.environ, VTX_GFX='cdp'):
                self.assertNotEqual(hygiene.gfx_dir(Ctx(p), cl), bad_dir)

    def test_one_second_all_qc_new_movie(self):
        p = project(self.root, 30)
        tl = json.loads(p.read_text())
        tl['blender_bridge'] = True
        (self.root / 'overlay.html').write_text('''<!doctype html><style>html,body{margin:0;background:transparent}
div{position:absolute;left:50px;top:20px;width:100px;height:50px;background:rgba(230,140,40,.8)}</style><div></div>''')
        tl['video_tracks'].append(dict(name='overlay', kind='gfx', clips=[dict(src='overlay.html', dst_in=0, dur=30)]))
        tl['sources'] = [dict(id='tone', path='tone.wav')]
        tl['audio_tracks'] = [dict(name='voice', events=[dict(src='tone', **{'in':0, 'out':30}, volume=1)])]
        with wave.open(str(self.root / 'tone.wav'), 'wb') as w:
            w.setparams((2, 2, 48000, 48000, 'NONE', 'not compressed'))
            w.writeframes(b''.join(struct.pack('<hh', *([round(6000*math.sin(2*math.pi*440*i/48000))]*2)) for i in range(48000)))
        p.write_text(json.dumps(tl))
        env = dict(os.environ, VTX_GFX_SPLIT_FRAMES='10', VTX_GFX_SPLIT_MAX='3')
        cmd = [sys.executable, '-m', 'vtx', '-t', str(p), 'all']
        (self.root / 'command.json').write_text(json.dumps(dict(command=cmd, env={k:v for k,v in env.items() if k.startswith('VTX_')}), indent=2))
        result = subprocess.run(cmd, capture_output=True, env=env, timeout=120)
        (self.root / 'all.log').write_bytes(result.stdout + result.stderr)
        (self.root / 'all.exitcode').write_text(str(result.returncode))
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))
        final = self.root / 'out/final.mp4'
        probe = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(final)], capture_output=True)
        (self.root / 'ffprobe.json').write_bytes(probe.stdout)
        self.assertEqual(probe.returncode, 0)
        streams = json.loads(probe.stdout)['streams']
        video = next(s for s in streams if s['codec_type'] == 'video')
        self.assertEqual(int(video['nb_frames']), 30)
        self.assertEqual((video['width'], video['height']), (192,96))
        self.assertTrue(any(s['codec_type'] == 'audio' for s in streams))
        receipt = json.loads((self.root / 'out/receipt.json').read_text())
        self.assertTrue(all(receipt['stages'][s]['ok'] for s in ('validate','prepare','gfx','video','audio','mux','qc')))
        self.assertEqual(receipt['stages']['gfx']['effective_backend'], 'electron')
        self.assertEqual(receipt['stages']['gfx']['path_counts'][gfx_backend.OUTPUT], 60)
        for stage in receipt['stages'].values(): self.assertEqual(stage['gfx_backend']['namespace'], 'integration_b0')
        self.assertTrue(list((self.root / 'cache/vseg_pass').glob('*.mkv')))
        self.assertTrue((self.root / 'out/qc_report.json').is_file())
        blend = self.root / 'preview.blend'
        self.assertTrue(blend.is_file(), 'all must execute the real finish hook')
        bridge = Path(__file__).resolve().parents[2] / 'apps/blender-bridge'
        verify_cmd = ['blender', '--background', '--factory-startup', '--python',
                      str(bridge / 'verify_blend.py'), '--', str(p), str(blend)]
        verify = subprocess.run(verify_cmd, capture_output=True, timeout=60)
        (self.root / 'verify.log').write_bytes(verify.stdout + verify.stderr)
        (self.root / 'verify.exitcode').write_text(str(verify.returncode))
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        # Read actual saved ImageStrip elements, not resolver-only/mock evidence.
        audit_code = f'''import bpy,json,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,{str(bridge.parents[1])!r})
from vtx.gfx_cache_reader import resolve_frames
root=Path({str(self.root)!r}); tl=json.loads((root/'timeline.json').read_text())
c=SimpleNamespace(root=root,cache=root/'cache',tl=tl,fps=30,w=192,h=96)
bpy.ops.wm.open_mainfile(filepath={str(blend)!r})
seq=bpy.context.scene.sequence_editor.strips
rows=[]
for track in tl['video_tracks']:
 for i,clip in enumerate(track['clips']):
  st=seq.get(track['name']+'|'+str(i)); assert st is not None
  actual=[(Path(st.directory)/e.filename).resolve() for e in st.elements]
  expected=[p.resolve() for p in resolve_frames(c,clip)]
  assert actual==expected,(actual,expected)
  assert st.frame_final_start==clip['dst_in'] and st.frame_final_end==clip['dst_in']+clip['dur']
  rows.append(dict(name=st.name,directory=st.directory,frames=len(actual),first=str(actual[0]),last=str(actual[-1])))
(root/'blender-path-audit.json').write_text(json.dumps(rows,indent=2))
'''
        audit_cmd = ['blender', '--background', '--factory-startup', '--python-expr', audit_code]
        audit = subprocess.run(audit_cmd, capture_output=True, timeout=60)
        (self.root / 'path-audit.log').write_bytes(audit.stdout + audit.stderr)
        (self.root / 'path-audit.exitcode').write_text(str(audit.returncode))
        (self.root / 'bridge-commands.json').write_text(json.dumps([verify_cmd, audit_cmd], indent=2))
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        self.assertEqual(len(json.loads((self.root / 'blender-path-audit.json').read_text())), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
