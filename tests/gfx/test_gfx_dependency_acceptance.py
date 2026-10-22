"""Harness source/file mocks only: never starts Chrome or FFmpeg."""
import copy
import gc
import weakref
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.runners.run_gfx_dependency_acceptance import fixture, check_trace, restore_edit, MAX_EVIDENCE, font_provenance, copy_font_with_notice, check_no_products, check_static_failure, check_native_events, check_round_counts, ProtocolObserver, evidence_url, prepare_font_observation, check_gc_contract
from vtx.render.gfx_dependencies import resolve, DependencyError
from vtx.infra import Ctx
from vtx.impact import capture, compare


class AcceptanceContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        font = self.root/'DejaVuSans.ttf'; font.write_bytes(b'unit-only-font-not-render-proof')
        notice = self.root/'copyright'
        notice.write_text('Format: test-only package manifest fixture\n\nFiles: *\nLicense: bitstream-vera\n Permission is hereby granted (synthetic unit fixture, not a real font).\n')
        self.font, self.notice = font, notice
        self.package_rows = self.package_fixture(font, notice)
        self.provenance = font_provenance(font,notice=notice,query=self.query)
        self.project = self.root/'project'; self.tl = fixture(self.project,font,self.provenance)
        timeline=self.project/'timeline.json';timeline.write_text(json.dumps(self.tl))
        self.c=Ctx(timeline)

    @staticmethod
    def package_fixture(font, notice):
        package='fonts-dejavu-core'
        return {
            ('-S',str(font)):package+': '+str(font)+'\n',
            ('-S',str(notice)):package+': '+str(notice)+'\n',
            ('-W','-f=${Package}\t${Version}\t${Status}\n',package):package+'\t2.37-test\tinstall ok installed\n',
            ('-L',package):str(font)+'\n'+str(notice)+'\n',
            ('--control-show',package,'md5sums'):'\n'.join(
                hashlib.md5(p.read_bytes(),usedforsecurity=False).hexdigest()+'  '+str(p).lstrip('/')
                for p in (font,notice))}

    def query(self,*args):
        return self.package_rows[args]

    def test_font_notice_complete_positive_and_metadata_only_boundary(self):
        record=font_provenance(self.font,notice=self.notice,query=self.query)
        self.assertEqual(record['package'],'fonts-dejavu-core')
        self.assertEqual(record['version'],'2.37-test')
        self.assertFalse(record['artifact_font_bytes'])
        self.assertEqual((self.project/'font-LICENSE.txt').read_bytes(),self.notice.read_bytes())
        self.assertEqual((self.project/'font.ttf').read_bytes(),self.font.read_bytes())

    def test_font_missing_notice_or_unmatched_package_fails_closed(self):
        with self.assertRaises(FileNotFoundError):
            font_provenance(self.font,notice=self.root/'absent',query=self.query)
        for key,value in [
            (('-S',str(self.notice)),'unrelated-font-package: '+str(self.notice)),
            (('-L','fonts-dejavu-core'),str(self.font)),
            (('--control-show','fonts-dejavu-core','md5sums'),'')]:
            with self.subTest(key=key):
                old=self.package_rows[key];self.package_rows[key]=value
                with self.assertRaises(ValueError):
                    font_provenance(self.font,notice=self.notice,query=self.query)
                self.package_rows[key]=old

    def test_font_changed_bytes_or_wrong_applicable_notice_is_not_assumed(self):
        original=self.font.read_bytes();self.font.write_bytes(original+b'changed')
        with self.assertRaisesRegex(ValueError,'manifest'):
            font_provenance(self.font,notice=self.notice,query=self.query)
        self.font.write_bytes(original)
        self.notice.write_text('Files: *\nLicense: unrelated\n Permission is hereby granted\n')
        self.package_rows=self.package_fixture(self.font,self.notice)
        with self.assertRaisesRegex(ValueError,'license stanza'):
            font_provenance(self.font,notice=self.notice,query=self.query)
        bad=copy.deepcopy(self.provenance);bad['notice_text']='altered notice'
        root=self.root/'bad-copy';root.mkdir()
        with self.assertRaises(ValueError):copy_font_with_notice(root,self.font,bad)
        self.assertFalse((root/'font.ttf').exists())

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
        good['requests'][0]['decision']='Fetch.failRequest'
        with self.assertRaises(AssertionError):check_trace(good,set(),rejected=True,target='file:///a.png')

    def test_worker_observes_real_policy_response_and_ack_with_mock_transport(self):
        from vtx import cli
        from vtx.render import gfx_render
        from vtx.render.gfx_dependencies import RequestPolicy
        from tests.runners.run_gfx_dependency_acceptance import worker
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
                        patch('tests.runners.run_gfx_dependency_acceptance.prepare_font_observation', return_value={}), \
                        patch.object(cli,'main',side_effect=invoke):
                    if injection:
                        with self.assertRaises(DependencyError):
                            worker(self.c.tl_path,trace,injection)
                    else:
                        worker(self.c.tl_path,trace)
                rows=json.loads(trace.read_text())
                check_trace(rows,set() if injection else {record['html_uri']},rejected=bool(injection),
                            target=rows.get('injected_target'))

    def test_fixture_all_nonzero_geometry_has_units_and_preserves_rois(self):
        import re
        css=(self.project/'global.css').read_text()
        for rule in ('width:1920px','height:1080px','width:24px','height:24px',
                     '#left{left:0}','#right{left:32px}','#shared{left:64px;',
                     'left:96px','top:24px','font:20px'):
            self.assertIn(rule,css)
        self.assertFalse(re.findall(r'(?:left|top|width|height|margin):[1-9][0-9]*(?=[;}])',css))

    def test_disabled_or_swallowed_production_rejection_cannot_pass_negative(self):
        from contextlib import nullcontext
        from vtx import cli
        from vtx.render import gfx_render
        from vtx.render.gfx_dependencies import RequestPolicy
        from tests.runners.run_gfx_dependency_acceptance import worker
        record=resolve(self.c,self.tl['video_tracks'][0]['clips'][0])
        for injection in ('local','external'):
            for regression in ('disabled','swallowed'):
                with self.subTest(injection=injection,regression=regression):
                    ws=gfx_render.WSConn.__new__(gfx_render.WSConn);ws.queue=[]
                    tab=gfx_render.Tab.__new__(gfx_render.Tab)
                    tab.ws=ws;tab._mid=0;tab._policy_commands=set();tab.request_policy=RequestPolicy(record)
                    def send(socket,data):
                        msg=json.loads(data)
                        if msg['method']=='Page.navigate':
                            socket.queue.append({'method':'Fetch.requestPaused','params':{
                                'requestId':'denial','resourceType':'Document','request':{'url':msg['params']['url']}}})
                        socket.queue.append({'id':msg['id'],'result':{}})
                    def recv(socket):return 1,json.dumps(socket.queue.pop(0))
                    def invoke():
                        try:tab.call('Fetch.enable')
                        except DependencyError:
                            if regression != 'swallowed':raise
                    trace=self.root/(injection+'-'+regression+'.json')
                    with patch.object(gfx_render.WSConn,'send_text',send), \
                            patch.object(gfx_render.WSConn,'recv_msg',recv), \
                            (patch.object(RequestPolicy,'check',return_value=None) if regression=='disabled' else nullcontext()), \
                            patch('tests.runners.run_gfx_dependency_acceptance.prepare_font_observation', return_value={}), \
                        patch.object(cli,'main',side_effect=invoke):
                        if regression=='disabled':
                            with self.assertRaisesRegex(AssertionError,'was not rejected'):
                                worker(self.c.tl_path,trace,injection)
                        else:
                            worker(self.c.tl_path,trace,injection)
                    row=json.loads(trace.read_text())
                    self.assertFalse(row['propagated_production_failure'])
                    self.assertTrue(row['injected'])
                    with self.assertRaises(AssertionError):
                        check_trace(row,set(),rejected=True,target=row['injected_target'])

    def test_rejection_contract_rejects_wrong_url_no_injection_capture_and_partial_file(self):
        target='file:///expected.html'
        row=dict(fallback_calls=0,injected=True,injected_target=target,injected_kind='Document',
            propagated_production_failure=True,terminal_exception_type='DependencyError',
            production_failures=[dict(reason='strict runtime request rejected')],
            screenshot_attempts=0,metrics=[],pending_acknowledgements=[],
            requests=[dict(url=target,kind='Document',decision='Fetch.failRequest',request_id='r')],
            acknowledgements=[dict(request_id='r',error=None,method='Fetch.failRequest')])
        check_trace(row,set(),rejected=True,target=target)
        for mutate in (lambda r:r.update(injected=False),lambda r:r.update(metrics=[{}]),
                       lambda r:r.update(screenshot_attempts=1),
                       lambda r:r.update(propagated_production_failure=False),
                       lambda r:r.update(terminal_exception_type='TimeoutError'),
                       lambda r:r['requests'][0].update(url='file:///unrelated.html')):
            bad=copy.deepcopy(row);mutate(bad)
            with self.assertRaises(AssertionError):check_trace(bad,set(),rejected=True,target=target)
        check_no_products(self.project,row)
        (self.project/'f0000.png').write_bytes(b'partial')
        with self.assertRaises(AssertionError):check_no_products(self.project,row)
        with self.assertRaises(AssertionError):check_static_failure(row,'undeclared dependency')

    def native_fixture(self,failed=False):
        from tests.execution.test_execution_events import Buffer
        from vtx.runtime import events
        stream=Buffer(); execution=events.ExecutionContext(stream)
        receipt={'stages':{},'timeline':hashlib.sha256(self.c.tl_path.read_bytes()).hexdigest()}
        error=DependencyError('strict runtime request rejected') if failed else None
        try:
            with execution.task('gfx',kind='stage').scope() as task:
                def action():
                    if error:raise error
                    return 0
                task.attempt(action)
                execution.receipt('gfx',True,lambda ref:receipt['stages'].update(
                    gfx=dict(ok=True,observed_by=ref)))
        except DependencyError:pass
        execution.finish(1 if failed else 0,error)
        path=self.root/'native.jsonl';path.write_text(stream.getvalue())
        identity={'head':'head','tree':'tree'}
        trace=dict(execution_run_id=execution.run_id,invocation=dict(stage='gfx',
            timeline=str(self.c.tl_path),timeline_sha256=hashlib.sha256(self.c.tl_path.read_bytes()).hexdigest(),
            **identity),propagated_production_failure=failed,native_error=events.safe_error(error),
            production_failures=[dict(native_error=events.safe_error(error))] if failed else [])
        return path,trace,receipt,identity

    def test_required_native_events_and_exact_receipt_identity(self):
        path,trace,receipt,identity=self.native_fixture()
        def validate():return check_native_events(path,trace,timeline=self.c.tl_path,
            stage='gfx',identity=identity,receipt=receipt,gfx_attempts=1)
        validate();original=path.read_text()
        for content in ('','{broken}\n',original.splitlines(True)[0],original[:-2]):
            path.write_text(content)
            with self.assertRaises((AssertionError,ValueError)):validate()
        path.unlink()
        with self.assertRaises(FileNotFoundError):validate()
        path.write_text(original)
        for target,key,value in ((trace,'execution_run_id','unrelated'),
                                 (trace['invocation'],'head','other'),
                                 (trace['invocation'],'stage','video'),
                                 (receipt,'timeline','wrong-timeline'),
                                 (receipt['stages']['gfx']['observed_by'],'run_id','other')):
            old=target[key];target[key]=value
            with self.assertRaises(AssertionError):validate()
            target[key]=old
        rows=[json.loads(line) for line in original.splitlines()]
        rows[-1]['extensions']['evidence_complete']=False
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        with self.assertRaises(AssertionError):validate()

    def test_native_failure_requires_matching_failed_attempt_and_cause(self):
        path,trace,receipt,identity=self.native_fixture(failed=True)
        def validate():return check_native_events(path,trace,timeline=self.c.tl_path,
            stage='gfx',identity=identity,failed=True,runtime=True)
        validate()
        with self.assertRaises(AssertionError):
            check_native_events(path,trace,timeline=self.c.tl_path,stage='gfx',identity=identity,
                failed=True,runtime=True,receipt={'timeline':trace['invocation']['timeline_sha256'],'stages':{'gfx':{'ok':True}}})
        trace['native_error']=None
        with self.assertRaises(AssertionError):validate()

    def test_real_cli_static_failure_and_validate_success_require_native_evidence(self):
        from tests.runners.run_gfx_dependency_acceptance import worker
        from vtx.render import gfx_render
        trace_path=self.root/'actual-cli.json'
        with patch.object(gfx_render.Browser,'start',side_effect=AssertionError('unexpected browser')) as browser:
            worker(self.c.tl_path,trace_path,stage='validate')
            tr=json.loads(trace_path.read_text())
            receipt=json.loads((self.project/'out/receipt.json').read_text())
            identity={k:tr['invocation'][k] for k in ('head','tree')}
            check_native_events(trace_path.with_suffix('.events.jsonl'),tr,timeline=self.c.tl_path,
                                stage='validate',identity=identity,receipt=receipt)
            negative=self.root/'actual-negative.json'
            (self.project/'a.html').write_text('<style/>')
            with self.assertRaises(SystemExit):worker(self.c.tl_path,negative)
            tr=json.loads(negative.read_text())
            check_static_failure(tr,'self-closing non-void HTML unsupported')
            check_no_products(self.project,tr)
            check_native_events(negative.with_suffix('.events.jsonl'),tr,timeline=self.c.tl_path,
                                stage='gfx',identity=identity,failed=True)
            browser.assert_not_called()

    def test_real_cli_runtime_failure_wrap_keeps_attempt_cause_and_failed_receipt(self):
        from tests.runners.run_gfx_dependency_acceptance import worker
        from tests.gfx._environment import bind_environment
        from vtx import cli
        from vtx.render import gfx_render
        from vtx.render.gfx_dependencies import RequestPolicy
        bind_environment(self)  # The renderer is fake; host browser identity is not under test.
        def reject(*args,**kwargs):
            policy=RequestPolicy(kwargs['dependency_record'])
            policy.violation=DependencyError('controlled mock runtime denial')
            policy.check()
        trace_path=self.root/'actual-runtime.json'
        with patch.object(cli,'budget_gate'),patch.object(gfx_render,'available',return_value=True), \
                patch.object(cli,'find_chrome',return_value='mock-chrome'), \
                patch.object(gfx_render,'render_page',side_effect=reject) as renderer:
            with self.assertRaises(SystemExit):worker(self.c.tl_path,trace_path)
        self.assertEqual(renderer.call_count,len(self.tl['video_tracks'][0]['clips']))
        self.assertEqual({call.args[0] for call in renderer.call_args_list},
                         {self.project/clip['src'] for clip in self.tl['video_tracks'][0]['clips']})
        tr=json.loads(trace_path.read_text())
        self.assertTrue(tr['propagated_production_failure'])
        self.assertEqual([(f['reason'], f['resource']) for f in tr['production_failures']],
                         [('strict runtime request rejected',
                           str(DependencyError('controlled mock runtime denial')))] * renderer.call_count)
        self.assertEqual(tr['terminal_exception_type'],'SystemExit')
        receipt=json.loads((self.project/'out/receipt.json').read_text())
        check_native_events(trace_path.with_suffix('.events.jsonl'),tr,timeline=self.c.tl_path,
            stage='gfx',identity={k:tr['invocation'][k] for k in ('head','tree')},
            receipt=receipt,failed=True,runtime=True)

    def test_business_success_with_missing_native_sink_is_insufficient(self):
        from tests.runners.run_gfx_dependency_acceptance import worker
        from vtx.runtime import events
        trace_path=self.root/'sink-failed.json'
        with patch.object(events,'open_sink',side_effect=OSError('controlled sink unavailable')):
            worker(self.c.tl_path,trace_path,stage='validate')
        tr=json.loads(trace_path.read_text())
        receipt=json.loads((self.project/'out/receipt.json').read_text())
        with self.assertRaises(FileNotFoundError):
            check_native_events(trace_path.with_suffix('.events.jsonl'),tr,timeline=self.c.tl_path,
                stage='validate',identity={k:tr['invocation'][k] for k in ('head','tree')},receipt=receipt)

    def test_exact_round_screenshot_counts_not_only_upper_bound(self):
        for changed,want in (([0,1],4),([],0),([0],2),([0,1],4)):
            row=dict(screenshot_attempts=want,metrics=[{}]*want)
            check_round_counts(row,changed)
            row['screenshot_attempts']+=1;row['metrics'].append({})
            with self.assertRaises(AssertionError):check_round_counts(row,changed)

    def test_protocol_observer_is_passive_and_allowlisted(self):
        rows = []
        observer = ProtocolObserver(lambda key, row: rows.append((key, row)))
        ws, other = object(), object()
        command = dict(id=3, method='CSS.enable', params={'headers': {'Cookie': 'secret'}})
        event = dict(method='Fetch.requestPaused', params=dict(requestId='fetch-1',
            frameId='frame-1', networkId='net-1', resourceType='Other',
            request=dict(url='file:///fixture/a.html?f=0', method='GET',
                         headers={'Authorization': 'secret'}, postData='secret'),
            responseHeaders=[{'name': 'cookie', 'value': 'secret'}]))
        originals = copy.deepcopy((command, event))
        observer.observe(ws, 'send', command)
        observer.observe(other, 'send', dict(id=3, method='Page.navigate', params={'url':'file:///fixture/b.html'}))
        observer.observe(ws, 'recv', event)
        observer.observe(ws, 'recv', dict(id=3, result={}))
        observer.observe(ws, 'recv', event)
        self.assertEqual((command, event), originals)
        self.assertNotIn('secret', json.dumps(rows))
        request = rows[2][1]
        self.assertEqual(request['pending_commands'], [dict(command_id=3, method='CSS.enable')])
        self.assertEqual(request['frame_id'], 'frame-1')
        self.assertEqual(request['network_id'], 'net-1')
        self.assertEqual(request['resource_type'], 'Other')
        self.assertEqual(rows[-1][1]['pending_commands'], [])
        self.assertEqual([r['sequence'] for _, r in rows], list(range(1, 6)))
        self.assertEqual(rows[0][1]['connection'], rows[2][1]['connection'])
        self.assertNotEqual(rows[0][1]['connection'], rows[1][1]['connection'])

    def test_protocol_connection_survives_close_and_rebuild(self):
        class Socket:
            def close(self):
                self.closed = True
        rows = []
        observer = ProtocolObserver(lambda key, row: rows.append(row))
        old = Socket(); ref = weakref.ref(old)
        observer.observe(old, 'send', dict(id=1, method='CSS.enable'))
        old.close(); del old; gc.collect()
        self.assertIsNotNone(ref())
        self.assertTrue(ref().closed)
        self.assertIs(observer.connections[id(ref())][0], ref())
        for _ in range(50):
            new = Socket()
            observer.observe(new, 'recv', dict(method='Fetch.requestPaused', params=dict(
                requestId='r', request={'url':'file:///fixture/a.html'}, resourceType='Other')))
            self.assertEqual(rows[-1]['pending_commands'], [])
            new.close(); del new
        self.assertEqual(len({row['connection'] for row in rows}), 51)

    def test_evidence_url_redacts_values_without_mutating_requests(self):
        url = 'https://user:PASSWORD@remote.invalid/a?f=12&access_token=SECRET#CREDENTIAL'
        safe = evidence_url(url)
        for secret in ('PASSWORD', 'SECRET', 'CREDENTIAL', 'user:'):
            self.assertNotIn(secret, safe)
        self.assertIn('/a?f=12&access_token=sha256', safe)
        self.assertEqual(evidence_url('file:///fixture/a.html?f=0'), 'file:///fixture/a.html?f=0')
        self.assertNotIn('SECRET', evidence_url('file:///fixture/a.html?f=SECRET'))
        self.assertNotIn('SECRET', evidence_url('https://[SECRET'))
        self.assertIsNone(evidence_url(None))
        rows = []; observer = ProtocolObserver(lambda key, row: rows.append(row))
        message = dict(method='Fetch.requestPaused', params=dict(request={'url':url}))
        original = copy.deepcopy(message)
        observer.observe(object(), 'recv', message)
        self.assertEqual(message, original)
        self.assertEqual(rows[0]['url'], safe)

    def test_font_observation_preparation_requires_owned_empty_target(self):
        values = {
            'Target.getTargetInfo': {'targetInfo': {'targetId':'owned','type':'page','url':'about:blank'}},
            'Page.getFrameTree': {'frameTree': {'frame': {'id':'owned','url':'about:blank'}}},
            'Runtime.evaluate': {'result': {'value': {'url':'about:blank','top':True,'empty':True}}},
            'DOM.enable': {}, 'CSS.enable': {}}
        calls = []
        def call(tab, method, **kwargs):
            calls.append(method)
            return copy.deepcopy(values[method])
        result = prepare_font_observation(object(), call, 'owned')
        self.assertEqual(result, dict(target_id='owned',frame_id='owned',url='about:blank'))
        self.assertEqual(calls, list(values))
        mutations = [
            lambda v: v['Target.getTargetInfo']['targetInfo'].update(targetId='foreign'),
            lambda v: v['Target.getTargetInfo']['targetInfo'].update(type='iframe'),
            lambda v: v['Target.getTargetInfo']['targetInfo'].update(url='file:///fixture.html'),
            lambda v: v['Page.getFrameTree']['frameTree']['frame'].update(url='file:///fixture.html'),
            lambda v: v['Page.getFrameTree']['frameTree']['frame'].update(id='foreign'),
            lambda v: v['Page.getFrameTree']['frameTree']['frame'].update(parentId='parent'),
            lambda v: v['Page.getFrameTree']['frameTree'].update(childFrames=[{}]),
            lambda v: v['Runtime.evaluate']['result']['value'].update(empty=False),
            lambda v: v['Runtime.evaluate']['result']['value'].update(top=False),
            lambda v: v['Runtime.evaluate']['result']['value'].update(url='file:///fixture.html'),
            lambda v: v['Runtime.evaluate'].update(exceptionDetails={'text':'error'})]
        original = copy.deepcopy(values)
        for mutation in mutations:
            values = copy.deepcopy(original); mutation(values); calls.clear()
            with self.assertRaises(AssertionError): prepare_font_observation(object(),call,'owned')
            self.assertNotIn('CSS.enable',calls)
            self.assertNotIn('Page.navigate',calls)
        calls.clear()
        with self.assertRaises(AssertionError): prepare_font_observation(object(),call,None)
        self.assertEqual(calls,[])
        values = original
        def unsupported(tab, method, **kwargs):
            if method == 'CSS.enable': raise RuntimeError('unsupported')
            return call(tab,method,**kwargs)
        with self.assertRaisesRegex(RuntimeError,'unsupported'):
            prepare_font_observation(object(),unsupported,'owned')

    def test_worker_prepares_owned_tab_before_fetch_and_never_reenables(self):
        from vtx import cli
        from vtx.render import gfx_render
        from tests.runners.run_gfx_dependency_acceptance import worker
        sent = []
        def init(ws, *args, **kwargs): ws.queue = []
        def send(ws, data):
            msg = json.loads(data); sent.append(msg['method'])
            result = {}
            if msg['method'] == 'Target.getTargetInfo':
                result = {'targetInfo': {'targetId':'owned','type':'page','url':'about:blank'}}
            elif msg['method'] == 'Page.getFrameTree':
                result = {'frameTree': {'frame': {'id':'owned','url':'about:blank'}}}
            elif msg['method'] == 'Runtime.evaluate':
                result = {'result': {'value': {'url':'about:blank','top':True,'empty':True}}}
            ws.queue.append({'id':msg['id'],'result':result})
        def recv(ws): return 1, json.dumps(ws.queue.pop(0))
        def invoke():
            tab = gfx_render.Tab(1, '/devtools/page/owned')
            tab.call('Fetch.enable', patterns=[{'urlPattern':'*'}])
            tab.call('Page.navigate', url='file:///fixture/a.html')
            with self.assertRaisesRegex(AssertionError,'prepared twice'): tab.call('Fetch.enable')
        path = self.root/'preparation-trace.json'
        with patch.object(gfx_render.WSConn,'__init__',init), \
             patch.object(gfx_render.WSConn,'send_text',send), \
             patch.object(gfx_render.WSConn,'recv_msg',recv), \
             patch.object(cli,'main',side_effect=invoke):
            worker(self.c.tl_path,path)
        self.assertEqual(sent.count('CSS.enable'),1)
        self.assertLess(sent.index('CSS.enable'),sent.index('Fetch.enable'))
        self.assertLess(sent.index('Fetch.enable'),sent.index('Page.navigate'))
        self.assertEqual(json.loads(path.read_text())['font_observation_targets'],
                         [dict(target_id='owned',frame_id='owned',url='about:blank')])

    def test_gc_contract_distinguishes_current_keys_and_disposable_vseg(self):
        from tests.runners.impact_inventory import observe
        from vtx.hygiene import gfx_dir, gc_plan
        from vtx import cli
        import os
        current = [gfx_dir(self.c, cl).relative_to(self.project).as_posix()
                   for cl in self.tl['video_tracks'][0]['clips']]
        files = [*(root+'/f0000.png' for root in current),
                 *(root+'/f0001.png' for root in current),
                 *(root+'/complete.json' for root in current),
                 'cache/gfx/ffffffffffff/f0000.png',
                 'cache/vseg/seg00_current.mp4', 'cache/vseg_qc/seg00_current.mp4',
                 'cache/vseg_pass/p0_123456789abc.mkv', 'cache/vseg_pass/p0_123456789abc.mkv.n',
                 'out/video_noaudio.mp4', 'out/receipt.json']
        for name in files:
            path = self.project/name; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'file-only-gc-fixture')
        before = observe(self.project)
        with patch.dict(os.environ, {'VTX_SEG_NVENC':'0'}):
            plan = gc_plan(self.c)
            planned = [p.relative_to(self.project).as_posix() for rows in plan.values() for p,_,_ in rows]
            with patch('sys.argv',['vtx','-t',str(self.c.tl_path),'gc']):
                self.assertIsNone(cli.main())
        after = observe(self.project)
        result = check_gc_contract(before,after,planned,current)
        self.assertIn('cache/vseg/seg00_current.mp4',result['removed_products'])
        self.assertIn(current[0]+'/f0001.png',result['protected'])
        for name in (current[0]+'/f0000.png','out/video_noaudio.mp4','out/receipt.json'):
            bad = copy.deepcopy(after)
            for group in bad.values(): group.pop(name,None)
            with self.assertRaises(AssertionError): check_gc_contract(before,bad,planned,current)
        bad = copy.deepcopy(after);bad['products'][current[0]+'/f0001.png']['sha256']='changed'
        with self.assertRaises(AssertionError): check_gc_contract(before,bad,planned,current)
        with self.assertRaises(AssertionError): check_gc_contract(before,after,planned+[current[0]],current)
        bad = copy.deepcopy(after);bad['products']['cache/vseg/seg00_current.mp4']=before['products']['cache/vseg/seg00_current.mp4']
        with self.assertRaises(AssertionError): check_gc_contract(before,bad,planned,current)
        bad = copy.deepcopy(after);bad['products'].pop('cache/vseg_pass/p0_123456789abc.mkv.n')
        with self.assertRaises(AssertionError): check_gc_contract(before,bad,planned,current)

    def test_workflow_scope_and_budget(self):
        root=Path(__file__).resolve().parents[2]
        workflow=(root/'.github/workflows/gfx-dependency-acceptance.yml').read_text()
        self.assertIn("      - 'tests/gfx/test_gfx_dependency_acceptance.py'", workflow)
        self.assertNotIn("      - 'tests/test_gfx_dependency_acceptance.py'", workflow)
        self.assertIn('contents: read',workflow)
        self.assertIn('head.repo.full_name == github.repository',workflow)
        self.assertIn("'dot/issue47-html-validation-'",workflow)
        self.assertIn('ref: ${{ github.event.pull_request.head.sha }}',workflow)
        self.assertIn('timeout-minutes: 10',workflow)
        for forbidden in ('pull_request_target','secrets.','--no-sandbox','add-apt-repository','apt-key','workflow_dispatch'):
            self.assertNotIn(forbidden,workflow)
        install = 'sudo apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core'
        self.assertIn(install, workflow)
        for tool in ('ffmpeg', 'ffprobe'):
            self.assertLess(workflow.index(install), workflow.index('command -v ' + tool))
            self.assertIn(tool + ' -version', workflow)
        harness = (root/'tests/runners/run_gfx_dependency_acceptance.py').read_text()
        self.assertIn("for tool in ('ffmpeg', 'ffprobe'):", harness)
        self.assertIn("run([tool, '-version'], tool + '-version', limit=10)", harness)
        self.assertEqual(MAX_EVIDENCE,20*1024*1024)


if __name__ == '__main__':
    unittest.main()
