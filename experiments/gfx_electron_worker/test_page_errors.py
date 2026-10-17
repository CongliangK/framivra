"""Focused Electron page-exception regressions; original navigation suite unchanged."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import uuid

from vtx.gfx_electron import PAGE_ERROR_GATE, WORKER_DIR, render_page


class GateUnitTests(unittest.TestCase):
    def test_host_gate_protocol(self):
        p = subprocess.run(['node', str(WORKER_DIR/'test_page_error_gate.mjs')],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)


@unittest.skipUnless(os.environ.get('VTX_TEST_ELECTRON') == '1', 'real Electron opt-in')
class RealPageErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PIL import Image
        cls.Image = Image
        root = os.environ.get('VTX_ELECTRON_EVIDENCE_DIR')
        cls.temp = None if root else tempfile.TemporaryDirectory(prefix='elec-errors-')
        cls.root = Path(root or cls.temp.name)/('errors-'+uuid.uuid4().hex[:12])
        cls.root.mkdir(parents=True)
        cls.results = []

    @classmethod
    def tearDownClass(cls):
        (cls.root/'test-results.json').write_text(
            json.dumps(cls.results, ensure_ascii=False, indent=2), encoding='utf-8')
        if cls.temp:
            cls.temp.cleanup()

    def setUp(self):
        self.case = self.root/self._testMethodName
        self.case.mkdir()

    def run_page(self, html, *, name='case', frames=(0,), stale=(0,), failed=(0,)):
        case = self.case/name
        case.mkdir()
        page = case/'原页 中文.html'
        page.write_text(html, encoding='utf-8')
        output = case/'frames'
        output.mkdir()
        old = {}
        for f in stale:
            target = output/f'f{f:04d}.png'
            self.Image.new('RGBA', (192,96), (1,2,3,255)).save(target, format='PNG')
            old[f] = target.read_bytes()
        ctx = SimpleNamespace(root=case, cache=case/'cache', out=case/'out')
        r = render_page(page, output, max(frames)+1, width=192, height=96,
                        frames=list(frames), ctx=ctx, timeout_s=3)
        self.results.append({'test':self._testMethodName, 'case':name, **r})
        self.assertEqual(r['failed'], list(failed), r)
        self.assertEqual(r['rendered'], len(frames)-len(failed), r)
        self.assertEqual(r['exit_code'], 1 if failed else 0, r)
        self.assertEqual(r['page_error_gate'], PAGE_ERROR_GATE, r)
        raw = json.loads((Path(r['evidence_dir'])/'result.json').read_text())
        self.assertEqual(raw['failed'], list(failed), raw)
        for f in failed:
            self.assertIn('page_runtime_error', r['errors'][str(f)], r)
            self.assertTrue(all(e['event'] in ('Runtime.exceptionThrown', 'Debugger.paused')
                                for e in raw['page_errors'][str(f)]))
            self.assertTrue(raw['page_errors'][str(f)])
            self.assertFalse(any(x['frame'] == f for x in raw['frames']), raw)
            if f in old:
                self.assertEqual((output/f'f{f:04d}.png').read_bytes(), old[f])
            else:
                self.assertFalse((output/f'f{f:04d}.png').exists())
        for receipt in raw['frames']:
            self.assertTrue(receipt['page_error_fence_passed'])
        self.assertFalse(list((case/'cache/tmp').glob('*')))
        self.assertFalse(list(case.rglob('*.tmp')))
        return r

    def test_document_start_throw_and_frame_isolation(self):
        # The independent review's actual red f1 fixture, with a subsequent good
        # frame to prove that the failed generation does not poison the next one.
        r = self.run_page('''<!doctype html><style>html,body{margin:0;background:rgb(210,0,0)}</style>
        <script>const f=Number(new URLSearchParams(location.search).get('f'));
        if(f===1)throw Error('FRAME_RENDER_FAILED_f1');
        document.documentElement.style.background='rgb(0,170,0)';</script>''',
                          frames=(0,1,2), stale=(1,), failed=(1,))
        self.assertIn('FRAME_RENDER_FAILED_f1', str(r['page_errors']['1']))
        for f in (0,2):
            image = self.Image.open(Path(r['evidence_dir']).parents[1]/f'f{f:04d}.png')
            self.assertEqual(image.convert('RGBA').getpixel((90,48)), (0,170,0,255))

    def test_document_start_syntax_error(self):
        r = self.run_page('''<!doctype html><style>html,body{background:rgb(210,0,0)}</style>
        <script>const broken = ; document.documentElement.style.background='green';</script>''')
        self.assertIn('SyntaxError', str(r['page_errors']['0']))

    def test_unhandled_rejection_error_and_primitive(self):
        for name, reason in [('error', "Error('UNHANDLED_RENDER')"), ('primitive', "'REJECT_PRIMITIVE'")]:
            with self.subTest(name=name):
                self.run_page(f'''<style>html{{background:rgb(210,0,0)}}</style><script>
                Promise.reject({reason});</script>''', name=name)

    def test_suppressed_unhandled_rejection(self):
        for name, reason in [('error', "Error('SUPPRESSED_UNHANDLED_RENDER')"),
                             ('primitive', "'SUPPRESSED_PRIMITIVE'")]:
            with self.subTest(name=name):
                r = self.run_page(f'''<script>
                addEventListener('unhandledrejection', e=>e.preventDefault());
                Promise.reject({reason});</script>''', name=name)
                self.assertTrue(any(e['event'] == 'Debugger.paused'
                                    for e in r['page_errors']['0']))

    def test_exceptions_during_witness_capture(self):
        for name, code in [('throw', "throw Error('CAPTURE_THROW')"),
                           ('rejection', "setTimeout(()=>Promise.reject('CAPTURE_REJECTION'),0)")]:
            with self.subTest(name=name):
                self.run_page(f'''<!doctype html><html><body></body><script>
                new MutationObserver(()=>{{{code}}}).observe(document.documentElement,{{childList:true}});
                </script></html>''', name=name, stale=())

    def test_console_errors_and_handled_rejection_are_not_exceptions(self):
        self.run_page('''<style>html,body{margin:0;background:rgb(12,35,78)}</style><script>
        console.log('normal');console.warn('Uncaught SyntaxError: not an exception event');
        console.error(new Error('LOGGED_ERROR_ONLY'));console.error('Uncaught (in promise) fake');
        Promise.reject(Error('HANDLED')).catch(()=>{});try{throw Error('CAUGHT')}catch(e){}
        debugger;
        </script>''', stale=(), failed=())

    def test_child_worker_exception_is_monitored(self):
        # A benign child must not be rejected merely for the auto-attach protocol.
        for name, source, failed in [('normal', 'postMessage(1)', ()),
                                     ('throw', "throw Error('WORKER_RENDER_FAILED')", (0,))]:
            with self.subTest(name=name):
                self.run_page(f'''<style>html{{background:rgb(12,35,78)}}</style><script>
                new Worker(URL.createObjectURL(new Blob([{json.dumps(source)}],{{type:'text/javascript'}})));
                </script>''', name=name, stale=(0,) if failed else (), failed=failed)


if __name__ == '__main__':
    unittest.main(verbosity=2)
