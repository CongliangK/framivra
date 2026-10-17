"""Electron-specific tests. Real browser suite requires VTX_TEST_ELECTRON=1.
Evidence root can be retained with VTX_ELECTRON_EVIDENCE_DIR; no video projects.
"""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from vtx.gfx_electron import ElectronRenderError, WORKER_DIR, render_page
from vtx.proc_supervisor import group_live_members, reap_orphan_workers


class ContractTests(unittest.TestCase):
    def test_reject_unsupported_without_launch(self):
        ctx = SimpleNamespace()
        for kwargs in ({'mode':'guess'}, {'output':'bitmap_shared_encoder'},
                       {'frames':[0,0]}, {'width':63}, {'frames':[True]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                render_page(__file__, '.', 2, ctx=ctx, **kwargs)
        with self.assertRaises(ElectronRenderError):
            render_page(__file__, '.', 1)
        with self.assertRaises(ElectronRenderError):
            render_page(__file__, '.', 1, ctx=ctx, electron_path='/nonexistent/electron')

    def test_pixel_witness_unit(self):
        p = subprocess.run(['node', str(WORKER_DIR/'test_pixel_codec.mjs')], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)

    def test_decoder_thread_protocol(self):
        p = subprocess.run(['node', str(WORKER_DIR/'test_decoder_thread.mjs')], capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)

    def test_png_codec_independent_reference(self):
        p = subprocess.run([sys.executable, '-B', str(WORKER_DIR/'test_pixel_codec_reference.py')],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)


@unittest.skipUnless(os.environ.get('VTX_TEST_ELECTRON') == '1', 'real Electron opt-in')
class RealNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PIL import Image
        cls.Image = Image
        root = os.environ.get('VTX_ELECTRON_EVIDENCE_DIR')
        cls.temp = None if root else tempfile.TemporaryDirectory(prefix='elec-test-')
        cls.root = Path(root or cls.temp.name)/('suite-'+uuid.uuid4().hex[:12])
        cls.root.mkdir(parents=True)
        cls.results=[]

    @classmethod
    def tearDownClass(cls):
        (cls.root/'test-results.json').write_text(json.dumps(cls.results, ensure_ascii=False, indent=2), encoding='utf-8')
        if cls.temp: cls.temp.cleanup()

    def setUp(self):
        self.case = self.root/self._testMethodName
        self.case.mkdir()
        self.ctx = SimpleNamespace(root=self.case, cache=self.case/'cache', out=self.case/'out')

    def render(self, page, frames=(0,), n=1, **kwargs):
        result=render_page(page,self.case/'输出 中文 very long output path with spaces',n,
                           width=192,height=96,frames=list(frames),ctx=self.ctx,**kwargs)
        self.results.append({'test':self._testMethodName,**result})
        # The existing supervisor must clean all runtime profiles and live trees.
        self.assertFalse(list((self.case/'cache/tmp').glob('*')))
        self.assertFalse(list(self.case.rglob('*.tmp')))
        return result

    def page(self, html):
        p=self.case/'普通 动效.html';p.write_text(html,encoding='utf-8');return p

    def test_navigation_generation_and_cross_recovery(self):
        p=self.case/('中文 long path '*8);p.mkdir()
        page=p/'ordinary.html';shutil.copyfile(WORKER_DIR/'pages/navigation_fixture.html',page)
        r=self.render(page, frames=[3,0,2], n=4)
        self.assertEqual(r['failed'],[],r)
        self.assertEqual(r['path_counts']['electron_native_topng'],3)
        for f in [3,0,2]:
            image=self.Image.open(Path(r['evidence_dir']).parents[1]/f'f{f:04d}.png').convert('RGBA')
            # Every top/bottom pixel, including BOTH marker regions: exact known
            # original pixels. A neighbor row or page-reported originals cannot pass.
            for y in (0,95):
                for x in range(192):
                    self.assertEqual(image.getpixel((x,y)),((x+f*17)%256,(y*7+f)%256,(x+y)%256,255))
            self.assertEqual(image.getpixel((180,50)),(0,0,0,0))
            rgba=image.getpixel((8,8))
            self.assertEqual(rgba[3],128)
            self.assertLessEqual(max(abs(a-b) for a,b in zip(rgba[:3],(200,100,40))),1)
        raw=json.loads((Path(r['evidence_dir'])/'result.json').read_text())
        self.assertEqual([x['generation'] for x in raw['frames']],[1,2,3])
        self.assertTrue(all(x['witnesses']==4 for x in raw['frames']))
        self.assertGreater(sum(x['rejected_paints'] for x in raw['frames']),0)

    def test_static_page_no_starvation_and_security(self):
        # Ordinary DOM/CSS, no optional APIs. Exposed Node or bridge turns red.
        page=self.page('''<style>html,body{margin:0;background:rgb(12,35,78)}</style>
        <script>if(typeof require!=='undefined'||typeof process!=='undefined'||window.vtxBridge)
        document.documentElement.style.background='red';</script>''')
        r=self.render(page,frames=[1,0],n=2)
        self.assertEqual(r['failed'],[],r)
        for f in [0,1]:
            im=self.Image.open(Path(r['evidence_dir']).parents[1]/f'f{f:04d}.png').convert('RGBA')
            self.assertEqual(set(im.get_flattened_data()),{(12,35,78,255)})

    def test_resource_ready_delayed_real_image(self):
        data=io.BytesIO();self.Image.new('RGBA',(192,96),(17,133,251,255)).save(data,format='PNG')
        payload=data.getvalue()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(.25);self.send_response(200);self.send_header('Content-Type','image/png');self.end_headers();self.wfile.write(payload)
            def log_message(self,*a): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            page=self.page(f'<style>body{{margin:0}}</style><img src="http://127.0.0.1:{server.server_port}/slow.png">')
            r=self.render(page);self.assertEqual(r['failed'],[],r)
            im=self.Image.open(Path(r['evidence_dir']).parents[1]/'f0000.png').convert('RGBA')
            self.assertEqual(im.getpixel((0,0)),(17,133,251,255))
        finally: server.shutdown();server.server_close();thread.join()

    def test_resource_error_never_old_file_success(self):
        page=self.page('<img src="does-not-exist.png">')
        output=self.case/'输出 中文 very long output path with spaces'
        output.mkdir();old=output/'f0000.png'
        self.Image.new('RGBA',(192,96),(1,2,3,255)).save(old,format='PNG')
        original_bytes=old.read_bytes()
        r=self.render(page);self.assertEqual(r['failed'],[0],r)
        self.assertEqual(r['rendered'],0)
        self.assertEqual(old.read_bytes(),original_bytes) # unchanged old file is NOT success
        self.assertIn('resource_error',str(r['errors']))

    def test_unstable_page_rejected(self):
        page=self.page('''<canvas id=c width=192 height=96></canvas><script>
        let f=0;setInterval(()=>{c.getContext('2d').fillStyle=`rgb(${++f%256},0,0)`;
        c.getContext('2d').fillRect(0,0,192,96)},5)</script>''')
        r=self.render(page);self.assertEqual(r['failed'],[0],r)
        self.assertIn('unstable_page',str(r['errors']))

    def test_marker_dom_side_effect_is_rejected(self):
        page=self.page('''<!doctype html><html><head><style>
        html{background:rgb(1,2,3)}body{margin:0;width:192px;height:96px;background:blue}
        body:last-child{background:rgb(12,35,78)}
        </style></head><body></body></html>''')
        r=self.render(page);self.assertEqual(r['failed'],[0],r)
        self.assertIn('marker_side_effect',str(r['errors']))
        self.assertFalse((Path(r['evidence_dir']).parents[1]/'f0000.png').exists())
        self.assertEqual(json.loads((Path(r['evidence_dir'])/'result.json').read_text())['frames'], [])

    def test_marker_has_third_child_is_rejected(self):
        page=self.page('''<!doctype html><html><head><style>
        html{background:rgb(1,2,3)}body{margin:0;width:192px;height:96px;background:rgb(12,35,78)}
        html:has(> :nth-child(3)) body{background:rgb(200,3,4)}
        </style></head><body></body></html>''')
        r=self.render(page);self.assertEqual(r['failed'],[0],r)
        self.assertIn('marker_side_effect',str(r['errors']))
        self.assertFalse((Path(r['evidence_dir']).parents[1]/'f0000.png').exists())
        self.assertEqual(json.loads((Path(r['evidence_dir'])/'result.json').read_text())['frames'], [])

    def test_marker_occlusion_timeout(self):
        page=self.page('<style>html{opacity:0!important}</style>never produces a valid marker')
        r=self.render(page,timeout_s=1)
        self.assertEqual(r['failed'],[0],r)
        self.assertIn('timeout',str(r['errors']))
        self.assertFalse((Path(r['evidence_dir']).parents[1]/'f0000.png').exists())

    def test_wrong_document_navigation_rejected(self):
        page=self.page('<script>location.search="?f=999"</script>')
        r=self.render(page,timeout_s=2)
        self.assertEqual(r['failed'],[0],r)

    def test_sigkill_host_reaps_real_electron_tree(self):
        page=self.page('<script>while(true){}</script>')
        code='''from pathlib import Path
from types import SimpleNamespace
import sys
from vtx.gfx_electron import render_page
p=Path(sys.argv[2]);c=SimpleNamespace(root=p,cache=p/'cache',out=p/'out')
render_page(sys.argv[1],p/'frames',1,width=192,height=96,ctx=c,timeout_s=120)
'''
        pgid=None
        with (self.case/'host.log').open('wb') as log:
            host=subprocess.Popen([sys.executable,'-c',code,str(page),str(self.case)],
                                  cwd=WORKER_DIR.parents[1],stdout=log,stderr=log)
            try:
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    ledgers=list((self.case/'cache/tmp').glob('*/.worker_pgid.json'))
                    if ledgers:
                        try:
                            ledger=json.loads(ledgers[0].read_text())
                            self.assertEqual(ledger['host_pid'],host.pid)
                            pgid=ledger['pgid']
                            if len(group_live_members(pgid))>=4: break
                        except (ValueError,OSError): pass
                    time.sleep(.025)
                self.assertIsNotNone(pgid,'real Electron did not start')
                members_before=group_live_members(pgid)
                self.assertGreaterEqual(len(members_before),4)
                host.kill();host.wait(timeout=5) # ONLY this test's direct child PID
                deadline=time.monotonic()+8
                while group_live_members(pgid) and time.monotonic()<deadline: time.sleep(.025)
                members_after=group_live_members(pgid)
                self.assertEqual(members_after,[])
                cleanup=reap_orphan_workers(self.ctx)
                self.assertEqual(cleanup['removed'],1)
                self.assertFalse(list((self.case/'cache/tmp').glob('*')))
                self.results.append({'test':self._testMethodName,'host_exit':host.returncode,
                                     'pgid':pgid,'members_before':members_before,
                                     'members_after':members_after,'cleanup':cleanup})
            finally:
                if host.poll() is None: host.kill();host.wait(timeout=5)
                reap_orphan_workers(self.ctx)

    def test_stuck_renderer_is_bounded(self):
        page=self.page('<script>while(true){}</script>')
        start=time.monotonic();r=self.render(page,timeout_s=1)
        self.assertEqual(r['failed'],[0],r)
        self.assertLess(time.monotonic()-start,8)


if __name__=='__main__': unittest.main(verbosity=2)
