"""Harness source/file mocks only: never starts Chrome or FFmpeg."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.run_gfx_dependency_acceptance import fixture, check_trace, restore_edit, MAX_EVIDENCE
from vtx.gfx_dependencies import resolve, DependencyError
from vtx.infra import Ctx
from vtx.impact import capture, compare


class AcceptanceContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        font = self.root/'source.ttf'; font.write_bytes(b'unit-only-font-not-render-proof')
        self.project = self.root/'project'; self.tl = fixture(self.project,font)
        timeline=self.project/'timeline.json';timeline.write_text(json.dumps(self.tl))
        self.c=Ctx(timeline)

    def test_formal_shape_two_aliases_nested_imports_and_direct_image(self):
        from PIL import Image
        self.assertEqual([self.c.w,self.c.h,self.c.fps,self.c.dur],[1920,1080,30,4])
        clips=self.tl['video_tracks'][0]['clips']
        a,b=[resolve(self.c,cl) for cl in clips]
        urls={r['url'] for r in a['resource_requests']}
        for relative in ('left/alias.css','right/alias.css','left/theme.css','right/theme.css',
                         'left/image.png','right/image.png'):
            self.assertIn((self.project/relative).as_uri(),urls)
        self.assertEqual((self.project/'left/alias.css').resolve(),(self.project/'right/alias.css').resolve())
        self.assertNotIn((self.project/'shared/theme.css').as_uri(),urls)
        self.assertIn((self.project/'direct.png').as_uri(),{r['url'] for r in b['resource_requests']})
        for side in ('left','right'):
            self.assertIn('#'+side+'{',(self.project/side/'theme.css').read_text())
            with Image.open(self.project/side/'image.png') as im:
                im.load(); self.assertEqual(im.size,(8,8))

    def test_restored_mtime_local_vs_global_actual_graph_paths(self):
        before=capture(self.tl,self.project,nseg=2)
        restore_edit(self.project/'left/theme.css',b'repeat:repeat',b'repeat:round ')
        after=capture(self.tl,self.project,nseg=2)
        local=compare(before,after)
        self.assertEqual(sum(p.startswith('cache/gfx/') for p in local['new_artifacts']),1)
        restore_edit(self.project/'global.css',b'#224466',b'#664422')
        global_change=compare(after,capture(self.tl,self.project,nseg=2))
        self.assertEqual(sum(p.startswith('cache/gfx/') for p in global_change['new_artifacts']),2)
        for namespace in ('vseg','vseg_qc','vseg_pass'):
            self.assertTrue(any(p.startswith('cache/'+namespace+'/') for p in local['new_artifacts']), namespace)

    def test_declared_wrong_real_css_base_cannot_replace_logical_target(self):
        (self.project/'shared/theme.css').write_text('body{color:red}')
        clip=copy.deepcopy(self.tl['video_tracks'][0]['clips'][0])
        clip['cache_dependencies'].remove('left/theme.css')
        clip['cache_dependencies'].append('shared/theme.css')
        with self.assertRaisesRegex(DependencyError,'undeclared'):
            resolve(self.c,clip)
        clip['cache_dependencies'].append('left/theme.css')
        r=resolve(self.c,clip)
        self.assertNotIn(str(self.project/'shared/theme.css'),r['references'])

    def test_style_variants_and_remote_reject_without_rewrite(self):
        for html in ('<style/>','<style />','<STYLE/>','<style id="s"/>',
                     '<table><style/></table>','<img src="https://dependency-control.invalid/x">',
                     '<img src="missing.png">'):
            (self.project/'a.html').write_text(html)
            with self.assertRaises(DependencyError):
                resolve(self.c,self.tl['video_tracks'][0]['clips'][0])
            self.assertEqual((self.project/'a.html').read_text(),html)

    def valid_trace(self):
        return dict(fallback_calls=0,pending_acknowledgements=[],
            requests=[dict(url='file:///a.png',decision='Fetch.fulfillRequest',request_id='r')],
            acknowledgements=[dict(request_id='r',error=None)],
            metrics=[dict(font=True,family='Fixture',platform_fonts=[dict(isCustomFont=True,glyphCount=4,familyName='DejaVu Sans')],faces=[dict(family='Fixture',status='loaded')],
                          images=[dict(complete=True,width=8,height=8)])])

    def test_trace_requires_actual_urls_ack_font_image_and_no_fallback(self):
        good=self.valid_trace();check_trace(good,{'file:///a.png'})
        mutations=[lambda t:t.update(metrics=[]),lambda t:t.update(acknowledgements=[]),
                   lambda t:t.update(fallback_calls=1),
                   lambda t:t['metrics'][0].update(font=False),
                   lambda t:t['metrics'][0].update(platform_fonts=[]),
                   lambda t:t['metrics'][0]['images'][0].update(width=0),
                   lambda t:t['acknowledgements'][0].update(error={'message':'denied'}),
                   lambda t:t['requests'][0].update(url='file:///other.png')]
        for mutate in mutations:
            t=copy.deepcopy(good);mutate(t)
            with self.assertRaises(AssertionError):check_trace(t,{'file:///a.png'})
        with self.assertRaises(AssertionError):check_trace(good,set(),rejected=True)
        good['requests'][0]['decision']='Fetch.failRequest';check_trace(good,set(),rejected=True)

    def test_worker_observes_real_policy_response_and_ack_with_mock_transport(self):
        from vtx import cli, gfx_render
        from vtx.gfx_dependencies import RequestPolicy
        from tests.run_gfx_dependency_acceptance import worker
        record=resolve(self.c,self.tl['video_tracks'][0]['clips'][0])
        for injection in (None, 'local', 'external'):
            with self.subTest(injection=injection):
                ws=gfx_render.WSConn.__new__(gfx_render.WSConn);ws.queue=[]
                tab=gfx_render.Tab.__new__(gfx_render.Tab)
                tab.ws=ws;tab._mid=0;tab._policy_commands=set();tab.request_policy=RequestPolicy(record)
                def send(socket,data):
                    msg=json.loads(data);method=msg['method'];result={}
                    if method=='Page.navigate':
                        socket.queue.append({'method':'Fetch.requestPaused','params':{
                            'requestId':'real-policy-request','resourceType':'Document',
                            'request':{'url':msg['params']['url']}}})
                    elif method=='Runtime.evaluate':
                        result={'result':{'value':self.valid_trace()['metrics'][0]}}
                    elif method=='DOM.getDocument':result={'root':{'nodeId':1}}
                    elif method=='DOM.querySelector':result={'nodeId':2}
                    elif method=='CSS.getPlatformFontsForNode':
                        result={'fonts':self.valid_trace()['metrics'][0]['platform_fonts']}
                    socket.queue.append({'id':msg['id'],'result':result})
                def receive(socket):return 1,json.dumps(socket.queue.pop(0))
                def invoke():
                    tab.call('Fetch.enable',patterns=[])
                    tab.call('Page.navigate',url=record['html_uri'])
                    tab.call('Page.captureScreenshot')
                trace=self.root/('trace-'+str(injection)+'.json')
                with patch.object(gfx_render.WSConn,'send_text',send), \
                        patch.object(gfx_render.WSConn,'recv_msg',receive), \
                        patch.object(cli,'main',side_effect=invoke):
                    if injection:
                        with self.assertRaises(DependencyError):
                            worker(self.c.tl_path,trace,injection)
                    else:
                        worker(self.c.tl_path,trace)
                rows=json.loads(trace.read_text())
                check_trace(rows,set() if injection else {record['html_uri']},rejected=bool(injection))

    def test_workflow_scope_and_budget(self):
        root=Path(__file__).resolve().parents[1]
        workflow=(root/'.github/workflows/gfx-dependency-acceptance.yml').read_text()
        self.assertIn('contents: read',workflow)
        self.assertIn('head.repo.full_name == github.repository',workflow)
        self.assertIn("'dot/issue47-html-validation-'",workflow)
        self.assertIn('ref: ${{ github.event.pull_request.head.sha }}',workflow)
        self.assertIn('timeout-minutes: 10',workflow)
        for forbidden in ('pull_request_target','secrets.','--no-sandbox','sudo','workflow_dispatch'):
            self.assertNotIn(forbidden,workflow)
        self.assertEqual(MAX_EVIDENCE,20*1024*1024)


if __name__ == '__main__':
    unittest.main()
