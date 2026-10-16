"""HTML rejection-checkpoint regressions; original 20 tests stay in their suites."""
import json
import os
from pathlib import Path
import unittest

from . import test_page_errors as page_errors


@unittest.skipUnless(os.environ.get('VTX_TEST_ELECTRON') == '1', 'real Electron opt-in')
class RealPromiseCheckpointTests(unittest.TestCase):
    setUpClass = page_errors.RealPageErrorTests.__dict__['setUpClass']
    tearDownClass = page_errors.RealPageErrorTests.__dict__['tearDownClass']
    setUp = page_errors.RealPageErrorTests.setUp
    run_page = page_errors.RealPageErrorTests.run_page

    def test_handled_before_checkpoint_immediate_microtask_same_task(self):
        r = self.run_page('''<!doctype html><style>html{background:rgb(210,0,0)}</style><script>
        const f=Number(new URLSearchParams(location.search).get('f'));
        addEventListener('unhandledrejection',()=>{document.documentElement.style.background='blue'});
        const p=Promise.reject(Error('HANDLED_BEFORE_CHECKPOINT'));
        const handled=()=>{document.documentElement.style.background='rgb(0,170,0)'};
        if(f===0)p.catch(handled);
        else if(f===1)queueMicrotask(()=>p.catch(handled));
        else { const sameTaskWork=Array.from({length:10},(_,i)=>i*i);p.catch(handled); }
        </script>''', frames=(0,1,2), stale=(), failed=())
        self.assertTrue(all(not x for x in r['page_errors'].values()), r)
        for f in (0,1,2):
            im=self.Image.open(Path(r['evidence_dir']).parents[1]/f'f{f:04d}.png')
            self.assertEqual(im.convert('RGBA').getpixel((90,48)), (0,170,0,255))

    def test_await_aggregate_and_synthetic_events_are_handled(self):
        self.run_page('''<style>html{background:rgb(0,170,0)}</style><script>
        (async()=>{
          try { await Promise.reject(Error('AWAIT_CAUGHT')); } catch(e) {}
          await Promise.all([Promise.reject('AGGREGATE_HANDLED')]).catch(()=>{});
        })().catch(()=>{});
        dispatchEvent(new PromiseRejectionEvent('unhandledrejection',
          {promise:Promise.resolve(),reason:'SYNTHETIC_NOT_UNHANDLED'}));
        debugger; // ordinary page breakpoints still harmless
        </script>''', stale=(), failed=())

    def test_suppressed_capture_rejections_are_still_fatal(self):
        for name, reason in [('error', "Error('CAPTURE_SUPPRESSED_ERROR')"),
                             ('primitive', "'CAPTURE_SUPPRESSED_PRIMITIVE'")]:
            with self.subTest(name=name):
                r = self.run_page(f'''<!doctype html><html><body></body><script>
                addEventListener('unhandledrejection',e=>{{
                  e.preventDefault();e.stopImmediatePropagation();e.promise.catch(()=>{{}});
                }},true);
                new MutationObserver((_records,o)=>{{o.disconnect();Promise.reject({reason});}})
                  .observe(document.documentElement,{{childList:true}});
                </script></html>''', name=name, stale=())
                self.assertTrue(any(e.get('confirmation') == 'trusted_html_event'
                                    for e in r['page_errors']['0']), r)

    def test_worker_checkpoint_and_suppression(self):
        for name, source, failed in [
            ('handled', "const p=Promise.reject('WORKER_HANDLED');queueMicrotask(()=>p.catch(()=>{}));", ()),
            ('error', "addEventListener('unhandledrejection',e=>{e.preventDefault();e.stopImmediatePropagation()},true);Promise.reject(Error('WORKER_UNHANDLED'));", (0,)),
            ('primitive', "addEventListener('unhandledrejection',e=>{e.preventDefault();e.stopImmediatePropagation()},true);Promise.reject('WORKER_PRIMITIVE');", (0,)),
        ]:
            with self.subTest(name=name):
                r = self.run_page(f'''<style>html{{background:rgb(0,170,0)}}</style><script>
                new Worker(URL.createObjectURL(new Blob([{json.dumps(source)}],{{type:'text/javascript'}})));
                </script>''', name=name, stale=(), failed=failed)
                if failed:
                    self.assertTrue(any(e.get('confirmation') == 'trusted_html_event'
                                        and e['session_id'] for e in r['page_errors']['0']), r)

    def test_subframe_checkpoint_and_suppression(self):
        for name, source, failed in [
            ('handled', "const p=Promise.reject('IFRAME_HANDLED');queueMicrotask(()=>p.catch(()=>{}));", ()),
            ('suppressed', "addEventListener('unhandledrejection',e=>{e.preventDefault();e.stopImmediatePropagation()},true);Promise.reject('IFRAME_UNHANDLED');", (0,)),
        ]:
            with self.subTest(name=name):
                self.run_page(f'''<style>html{{background:rgb(0,170,0)}}</style><iframe style="display:none"
                srcdoc="<script>{source}</script>"></iframe>''', name=name, stale=(), failed=failed)


if __name__ == '__main__':
    unittest.main(verbosity=2)
