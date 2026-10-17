"""CEF Alpha production route + real native Mode N/opaque/transparent pixels.

VTX_TEST_CEF_WORKER enables real CEF (no Chrome/IME service involved).
VTX_CEF_EVIDENCE_DIR optionally retains the complete fixture and native logs.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from vtx import cli, gfx_backend, gfx_cef, hygiene
from vtx.cache_state import receipt_fingerprint
from vtx.composite import RenderPlan
from vtx.infra import Ctx
from tests.test_cef_frame_sync import decode_png_rgba

ROOT = Path(__file__).resolve().parents[1]
WORKER = os.environ.get('VTX_TEST_CEF_WORKER', '')

# Intentionally NO __vtx_render or __vtx_originals. All four corners have
# differing pixels, not equal to adjacent rows: neighbor-copy would fail.
PAGE = '''<!doctype html><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}
i{position:fixed;width:1px;height:1px;display:block}
</style><script>
let f=Number(new URLSearchParams(location.search).get('f'));
if(f%2)document.documentElement.style.background='rgb(17,33,65)';
for(let y=0;y<48;y++)for(let x=0;x<64;x++){
 if(x>9 && x<54 && y>9 && y<38)continue;
 let e=document.createElement('i');
 e.style.cssText=`left:${x}px;top:${y}px;background:rgb(${(x+f)%256},${y+9},${x^y})`;
 document.documentElement.appendChild(e);
}
</script>'''


def project(root, n=3):
    (root / 'page.html').write_text(PAGE)
    p = root / 'timeline.json'
    p.write_text(json.dumps(dict(version=2,
        project=dict(fps=30, width=64, height=48, duration_frames=n), sources=[],
        subtitles=[], audio_tracks=[], video_tracks=[dict(name='gfx', kind='gfx',
            clips=[dict(src='page.html', dst_in=0, dur=n)])])))
    return p


class Routing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cef_alpha_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.p = project(self.root)
        self.worker = self.root / 'worker'
        self.worker.write_text('#!/bin/sh\nexit 99\n')
        self.worker.chmod(0o755)
        self.env = mock.patch.dict(os.environ, VTX_GFX='cdp', VTX_GFX_CACHE_NS='',
                                   VTX_CEF_WORKER=str(self.worker), VTX_CEF_MODE='a0')
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_unknown_backend_fail_closed(self):
        for value in ('electron-typo', 'typo', ''):
            with mock.patch.dict(os.environ, VTX_GFX=value):
                with self.assertRaisesRegex(ValueError, 'no silent fallback'):
                    Ctx(self.p)

    def test_missing_worker_fails_before_render(self):
        with mock.patch.dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=''):
            with self.assertRaisesRegex(ValueError, 'VTX_CEF_WORKER'):
                Ctx(self.p)

    def test_backend_namespace_and_receipt_identity(self):
        base = Ctx(self.p)
        cl = base.tl['video_tracks'][0]['clips'][0]
        base_key = hygiene.gfx_key(base, cl)
        with mock.patch.dict(os.environ, VTX_GFX='cef'):
            cef = Ctx(self.p)
            self.assertNotEqual(base_key, hygiene.gfx_key(cef, cl))
            self.assertIn('gfx_exp/cef-alpha/', str(hygiene.gfx_dir(cef, cl)))
            self.assertNotEqual(base.input_fingerprint, cef.input_fingerprint)
            first = receipt_fingerprint(cef)
            with mock.patch.dict(os.environ, VTX_CEF_MODE='a1'):
                self.assertNotEqual(first, receipt_fingerprint(cef))
            with mock.patch.dict(os.environ, VTX_GFX_CACHE_NS='experiment2'):
                self.assertNotEqual(first, receipt_fingerprint(cef))
            self.worker.write_text('#!/bin/sh\nexit 98\n')
            self.assertNotEqual(first, receipt_fingerprint(cef))

    def test_downstream_segment_keys_include_namespace_route(self):
        plan = SimpleNamespace(c=SimpleNamespace(cache=self.root / 'cache'),
                               W=64, H=48, draft=False)
        from vtx.frame_source import png_source
        c = Ctx(self.p)
        clip = c.tl['video_tracks'][0]['clips'][0]
        with mock.patch.dict(os.environ, VTX_GFX_CACHE_NS='one'):
            source = png_source(c, clip)
        layer = dict(gfx=True, source=source, cl={}, x=0, y=0, w2=64, h2=48)
        k = RenderPlan._seg_content_key(plan, 0, 3, [layer], False, None)
        with mock.patch.dict(os.environ, VTX_GFX_CACHE_NS='two'):
            layer['source'] = png_source(c, clip)
        self.assertEqual(source.directory.name, layer['source'].directory.name)
        self.assertNotEqual(k, RenderPlan._seg_content_key(plan, 0, 3, [layer], False, None))

    def test_cef_never_falls_back_after_native_failure(self):
        with mock.patch.dict(os.environ, VTX_GFX='cef'), \
                mock.patch.object(gfx_cef, 'render_page', side_effect=RuntimeError('native failed')), \
                mock.patch.object(cli.gfx_cli, 'render_cli') as fallback, \
                mock.patch('vtx.gfx_render.render_page') as cdp:
            c = Ctx(self.p)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                cli.gfx(c)
            fallback.assert_not_called()
            cdp.assert_not_called()
            rec = json.loads((self.root / 'out/receipt.json').read_text())
            self.assertFalse(rec['stages']['gfx']['ok'])

    def test_dry_cef_does_not_start_worker(self):
        with mock.patch.dict(os.environ, VTX_GFX='cef'), \
                mock.patch.object(gfx_cef, 'render_page') as render:
            c = Ctx(self.p)
            c.dry = True
            with contextlib.redirect_stdout(io.StringIO()):
                cli.gfx(c)
            render.assert_not_called()
            self.assertFalse(c.cache.exists())


@unittest.skipUnless(WORKER and Path(WORKER).is_file(), 'requires VTX_TEST_CEF_WORKER')
class NativeProduction(unittest.TestCase):
    def _assert_structure_css_veto(self, name, css):
        from vtx.cef_client import CefWorkerError
        from vtx.proc_supervisor import SupervisedProcessGroup
        with tempfile.TemporaryDirectory(prefix=f'cef_dom_{name}_') as td:
            root = Path(td)
            p = project(root, n=1)
            page = root / 'page.html'
            page.write_text('''<!doctype html><html><head><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}
#content{position:fixed;inset:0}''' + css + '''
</style></head><body><div id="content"></div></body></html>''')
            env = dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=WORKER,
                       VTX_CEF_MODE='a0', VTX_GFX_CACHE_NS='')
            with mock.patch.dict(os.environ, env):
                c = Ctx(p)
                fds, worker_env = [], dict(os.environ)
                def argv(run, profile):
                    fd = os.open(run, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    fds.append(fd)
                    worker_env['TMPDIR'] = f'/proc/{os.getpid()}/fd/{fd}'
                    return [WORKER, '--mode=a0', f'--url={page.as_uri()}?f=0',
                            '--width=64', '--height=48', '--begin-frames=12',
                            '--out-png=baseline.png', '--out-json=baseline.json',
                            f'--root-cache-path={profile}']
                try:
                    with (root/'baseline.worker.log').open('wb') as log:
                        with SupervisedProcessGroup(c, argv, backend_id='cef_dom_baseline',
                                                    env=worker_env, stdout=log, stderr=log) as group:
                            self.assertEqual(group.proc.wait(timeout=60), 0)
                            shutil.copyfile(group.run_dir/'baseline.png', root/'baseline.png')
                finally:
                    for fd in fds:
                        os.close(fd)
                w, h, baseline = decode_png_rgba(root/'baseline.png')
                self.assertEqual((w, h), (64, 48))
                self.assertEqual(baseline, bytes((17, 231, 47, 255)) * (w * h))
                out = hygiene.gfx_dir(c, c.tl['video_tracks'][0]['clips'][0])
                out.mkdir(parents=True)
                with self.assertRaises(CefWorkerError):
                    gfx_cef.render_page(page, out, 1, ctx=c, width=w, height=h)
                self.assertFalse((out/'f0000.png').exists(), 'vetoed pixels published')
                events = [json.loads(line) for path in (c.cache/'reports/cef').glob('*.sync.jsonl')
                          for line in path.read_text().splitlines()]
                self.assertTrue(any(e['event'] == 'exact_first_verified' for e in events))
                self.assertTrue(any(e['event'] == 'exact_baseline_veto_armed' and
                                    e['role'] == 'equality_veto_only' and
                                    e['restoration_source'] is False for e in events))
                self.assertTrue(any(e['event'] == 'exact_baseline_mismatch' and
                                    e['reason'] == 'probe_content_perturbation' for e in events))
                self.assertFalse(any(e.get('to') == 'S5_ONPAINT_VERIFIED' for e in events))
                self.assertTrue(any(e['event'] == 'request_end' and e['status'] == 'failed'
                                    for e in events))
                receipts = [json.loads(path.read_text())
                            for path in (c.cache/'reports/cef').glob('*.receipts.json')]
                self.assertEqual(receipts, [[]])
                self.assertFalse(any((c.cache/'tmp').iterdir()), 'supervised worker leaked')
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                shutil.copytree(root, Path(evidence)/f'dom-{name}', dirs_exist_ok=True)

    def test_structure_css_body_last_child_is_vetoed(self):
        self._assert_structure_css_veto('last_child',
            '#content{background:rgb(231,17,31)}'
            'body:last-child #content{background:rgb(17,231,47)}')

    def test_structure_css_html_has_third_child_is_vetoed(self):
        self._assert_structure_css_veto('has_third_child',
            '#content{background:rgb(17,231,47)}'
            'html:has(> :nth-child(3)) #content{background:rgb(231,17,31)}')

    def test_large_blurred_page_does_not_certify_initial_blank(self):
        # Real original-page failure shape: full-size opaque background behind
        # large software-rasterized blurred gradients. NO page render/originals
        # hooks; capture may first receive several identical transparent paints.
        with tempfile.TemporaryDirectory(prefix='cef_blur_') as td:
            root = Path(td)
            p = project(root, n=1)
            data = json.loads(p.read_text())
            data['project'].update(width=1920, height=1080)
            p.write_text(json.dumps(data))
            (root/'page.html').write_text('''<!doctype html><html><head><style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1920px;height:1080px;background:transparent;overflow:hidden}
#bg{position:absolute;inset:0;background:#faf9f6}
.blob{position:absolute;width:980px;height:980px;border-radius:50%;filter:blur(150px);opacity:.10}
.b1{left:-220px;top:-280px;background:radial-gradient(circle,#c8a24a,transparent 65%)}
.b2{right:-260px;bottom:-320px;background:radial-gradient(circle,#d97706,transparent 65%);opacity:.07}
</style></head><body><div id="bg"></div><div class="blob b1"></div><div class="blob b2"></div></body></html>''')
            env = dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=WORKER,
                       VTX_CEF_MODE='a0', VTX_GFX_CACHE_NS='')
            with mock.patch.dict(os.environ, env):
                c = Ctx(p)
                out = root/'frames'
                out.mkdir()
                gfx_cef.render_page(root/'page.html', out, 1, ctx=c,
                                    width=1920, height=1080)
                w, h, rgba = decode_png_rgba(out/'f0000.png')
                self.assertEqual((w,h), (1920,1080))
                self.assertEqual(rgba[3::4], b'\xff' * (w*h), 'initial blank accepted')
                self.assertGreater(len(set(zip(rgba[0::4],rgba[1::4],rgba[2::4]))), 20)
                events = [json.loads(line) for path in (c.cache/'reports/cef').glob('*.sync.jsonl')
                          for line in path.read_text().splitlines()]
                self.assertEqual(sum(e.get('to') == 'S5_ONPAINT_VERIFIED' for e in events), 1)
                self.assertEqual(sum(e['event'] == 'exact_baseline_equal' and
                                     e['compared_bytes'] == w*h*4 for e in events), 1)
                self.assertFalse(any((c.cache/'tmp').iterdir()), 'unsigned browser leaked')
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                shutil.copytree(root, Path(evidence)/'large-blur', dirs_exist_ok=True)

    def test_page_protocol_failures_are_native_timeouts(self):
        from vtx.cef_client import CefWorkerClient, CefWorkerError
        from vtx.proc_supervisor import SupervisedProcessGroup
        with tempfile.TemporaryDirectory(prefix='cef_protocol_') as td:
            root = Path(td)
            p = project(root)
            c = Ctx(p)
            for mode, fault in [('p', 'none'), ('n', 'stale_canary'), ('n', 'no_ready')]:
                with self.subTest(mode=mode, fault=fault):
                    fds, env = [], dict(os.environ)
                    def argv(run, profile):
                        fd = os.open(run, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                        fds.append(fd)
                        env['TMPDIR'] = f'/proc/{os.getpid()}/fd/{fd}'
                        return [WORKER, '--mode=a0', '--serve', '--sync',
                                f'--sync-mode={mode}', '--sync-canary=exact',
                                f'--sync-fault={fault}', '--sync-ready-timeout-ms=300',
                                '--sync-pump-timeout-ms=300', '--width=64', '--height=48',
                                '--ipc-socket=ipc.sock', f'--root-cache-path={profile}',
                                f'--url-template={(root/"page.html").as_uri()}?f=' + ('0' if mode == 'p' else '{f}')]
                    try:
                        with SupervisedProcessGroup(c, argv, backend_id='cef_protocol', env=env) as group:
                            with CefWorkerClient.connect(group.run_dir/'ipc.sock') as client:
                                with self.assertRaises(CefWorkerError) as caught:
                                    client.request_png(17, 'frame.png', request_id=1,
                                                       worker_relative=True, timeout=10)
                                self.assertEqual(caught.exception.status_code, 1)
                            self.assertEqual(group.proc.wait(timeout=15), 0)
                    finally:
                        for fd in fds:
                            os.close(fd)

    def test_full_pipeline_fixture(self):
        import math
        import struct
        import wave
        with tempfile.TemporaryDirectory(prefix='cef_pipeline_') as td:
            root = Path(td)
            p = project(root, n=30)
            (root / 'page.html').write_text('''<!doctype html><style>
html,body{margin:0;background:#faf9f6;overflow:hidden}div{position:fixed;
width:80px;height:40px;top:60px;background:#c8a24a}</style><div></div><script>
let f=+new URLSearchParams(location.search).get('f');
document.querySelector('div').style.left=(20+f*3)+'px';</script>''')
            with wave.open(str(root / 'voice.wav'), 'wb') as w:
                w.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                w.writeframes(b''.join(struct.pack('<h', int(4000*math.sin(i*2*math.pi*440/48000)))
                                       for i in range(48000)))
            tl = json.loads(p.read_text())
            tl['project'].update(width=320, height=180)
            tl['sources'] = [dict(id='voice', path='voice.wav')]
            tl['audio_tracks'] = [dict(name='voice', events=[dict(src='voice', volume=1.0, **{'in': 0, 'out': 30})])]
            p.write_text(json.dumps(tl))
            env = dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=WORKER,
                       VTX_CEF_MODE='a0', VTX_GFX_PAGES='1', VTX_GFX_CACHE_NS='',
                       VTX_PIPELINE='0', VTX_VIDEO_SEGMENTS='1')
            result = subprocess.run([os.sys.executable, str(ROOT/'vtx/vtx.py'), '-t', str(p), 'all'],
                                    env=env, capture_output=True, text=True, timeout=180)
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                dest = Path(evidence) / 'pipeline-fixture'
                shutil.copytree(root, dest, dirs_exist_ok=True)
                (dest/'test.stdout.log').write_text(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rec = json.loads((root/'out/receipt.json').read_text())
            for stage in ('validate', 'prepare', 'gfx', 'video', 'audio', 'mux', 'qc'):
                self.assertTrue(rec['stages'][stage]['ok'], stage)
            self.assertTrue((root/'out/final.mp4').is_file())

    def test_real_mode_n_exact_pixels_and_cache_repair(self):
        with tempfile.TemporaryDirectory(prefix='cef_production_') as td:
            root = Path(td)
            p = project(root)
            env = dict(os.environ, VTX_GFX='cef', VTX_CEF_WORKER=WORKER,
                       VTX_CEF_MODE='a0', VTX_GFX_PAGES='1', VTX_GFX_CACHE_NS='')
            command = [os.sys.executable, str(ROOT / 'vtx/vtx.py'), '-t', str(p),
                       '--events-jsonl', str(root / 'cef-events.jsonl'), 'gfx']
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=150)
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                shutil.copytree(root, Path(evidence) / 'production-fixture', dirs_exist_ok=True)
                (Path(evidence) / 'production-fixture/startup.log').write_text(result.stdout + result.stderr)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('Mode N native-exact', result.stdout)
            from vtx import events as execution_events
            observed = execution_events.read_events((root / 'cef-events.jsonl').read_bytes())
            self.assertTrue(observed['complete'])
            self.assertTrue(observed['events'][-1]['extensions']['evidence_complete'])
            blocks = [row for row in observed['events'] if row['event'] == 'task_start'
                      and 'backend' in row['extensions']]
            self.assertEqual([row['extensions']['backend'] for row in blocks], ['cef'])
            (root / 'cef-events.initial.jsonl').write_bytes((root / 'cef-events.jsonl').read_bytes())
            with mock.patch.dict(os.environ, env):
                c = Ctx(p)
                out = hygiene.gfx_dir(c, c.tl['video_tracks'][0]['clips'][0])
            for f in range(3):
                w, h, rgba = decode_png_rgba(out / f'f{f:04d}.png')
                self.assertEqual((w, h), (64, 48))
                for y in range(h):
                    for x in range(w):
                        off = (y * w + x) * 4
                        if x <= 9 or x >= 54 or y <= 9 or y >= 38:
                            want = bytes(((x + f) % 256, y + 9, x ^ y, 255))
                        else:
                            want = bytes((17, 33, 65, 255) if f % 2 else (0, 0, 0, 0))
                        self.assertEqual(rgba[off:off + 4], want, (f, x, y))
            events = [json.loads(s) for path in (root / 'cache/reports/cef').glob('*.sync.jsonl')
                      for s in path.read_text().splitlines()]
            accepted = [e for e in events if e.get('to') == 'S5_ONPAINT_VERIFIED']
            self.assertEqual(len(accepted), 3)
            self.assertTrue(all(e['drive_mode'] == 'n' and
                                e['restore_source'] == 'native_cross_capture' for e in accepted))
            for accepted_event in accepted:
                request = [e for e in events if e['request_id'] == accepted_event['request_id']]
                baseline = [e for e in request if e['event'] == 'exact_baseline_veto_armed']
                equal = [e for e in request if e['event'] == 'exact_baseline_equal']
                self.assertEqual(len(baseline), 1)
                self.assertEqual(len(equal), 1)
                self.assertIs(baseline[0]['restoration_source'], False)
                self.assertEqual(equal[0]['compared_bytes'], 64 * 48 * 4)
                self.assertLess(equal[0]['seq'], accepted_event['seq'])
            cached = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(cached.returncode, 0, cached.stdout + cached.stderr)
            self.assertIn('已缓存', cached.stdout)
            (out / 'f0001.png').write_bytes(b'corrupt')
            repair = subprocess.run(command, env=env, capture_output=True, text=True, timeout=150)
            self.assertEqual(repair.returncode, 0, repair.stdout + repair.stderr)
            self.assertIn('native-exact×1', repair.stdout)
            self.assertEqual(decode_png_rgba(out / 'f0001.png')[2][:4], bytes((1, 9, 0, 255)))
            self.assertFalse(any((root / 'cache/tmp').iterdir()), 'supervised profiles leaked')
            evidence = os.environ.get('VTX_CEF_EVIDENCE_DIR')
            if evidence:
                target = Path(evidence) / 'production-fixture'
                shutil.copytree(root, target, dirs_exist_ok=True)
                (target / 'test.stdout.log').write_text(result.stdout + '\n' + repair.stdout)


if __name__ == '__main__':
    unittest.main()
