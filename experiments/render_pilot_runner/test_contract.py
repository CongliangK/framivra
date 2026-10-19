"""Explicit offline tests. No Chrome, network, media skip gates or CI registration."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from experiments.render_pilot_runner import contract as c
from experiments.render_pilot_runner import quality as q
from experiments.render_pilot_runner.runner import fresh

class ContractTests(unittest.TestCase):
    def test_targets_and_budget(self):
        self.assertEqual(len(c.TARGETS),120);self.assertEqual(len(set(c.TARGETS)),120)
        self.assertEqual(c.BATCHES['continuous'],list(range(1380,1470)))
        self.assertEqual(len(c.BATCHES['diagnostic']),30);self.assertEqual(c.DEADLINE_SECONDS,900)

    def test_global_local_inactive_mapping(self):
        wing={'windowStart':18,'duration':3582};body={'windowStart':0,'duration':3600}
        self.assertEqual(len(c.mapped(wing,c.TARGETS)),118)
        self.assertEqual(len(c.mapped(body,c.TARGETS)),120)
        self.assertEqual(c.mapped(wing,[17,18,19,3599]),[
            {'global':18,'local':0},{'global':19,'local':1},{'global':3599,'local':3581}])
        self.assertTrue(all(x['local']==x['global'] for x in c.mapped(body,c.TARGETS)))

    def test_invalid_frames(self):
        for targets in ([0,0],[-1],[3600],[False],[1.5]):
            with self.subTest(targets=targets),self.assertRaises(ValueError):
                c.mapped({'windowStart':0,'duration':3600},targets)

    def test_same_png_arm_jobs(self):
        bound={'root':'/unused','entries':[{'entryId':'T08.01','src':'w.html','windowStart':18,'duration':3582},
                                        {'entryId':'T07.01','src':'b.html','windowStart':0,'duration':3600}]}
        p=c.build_plan(bound,'fast-png');self.assertEqual(len(p['jobs']),8)
        for a,b in zip(p['jobs'][:4],p['jobs'][4:]):self.assertEqual(a['mapping'],b['mapping'])
        self.assertEqual(sum(j['expected_rendered'] for j in p['jobs'] if j['arm']=='baseline'),238)
        with self.assertRaises(ValueError):c.build_plan(bound,'group+fast-png')

    def stats(self):
        return {'rendered':3,'failed':[],'tabs':1,'canvas_export':False,
                'path_counts':{'override':3,'canvas':0,'navfree':0,'twoshot':0,'opaque':0,
                               'canvas_fallback':0,'failed':0,'static_copied':0},
                'timings_ms':{k:{'sum':12.0,'p50':4.0,'p95':5.0} for k in ('nav','export','decode','write')}}

    def test_exact_timings_and_explicit_count(self):
        s=self.stats();out=c.validate_stats(s,3)
        self.assertEqual(out['export']['sum'],12.0);self.assertEqual(out['export']['p95'],5.0)
        self.assertEqual(out['export']['count'],3)
        self.assertEqual(set(s['timings_ms']['export']),{'sum','p50','p95'})

    def test_fallback_cache_empty_failure_nan(self):
        for k,v in [('rendered',0),('failed',[0]),('tabs',2),('canvas_export',True)]:
            s=self.stats();s[k]=v
            with self.subTest(k=k),self.assertRaises(ValueError):c.validate_stats(s,3)
        for path in ('static_copied','canvas','navfree','twoshot','opaque','failed','canvas_fallback'):
            s=self.stats();s['path_counts'][path]=1
            with self.subTest(path=path),self.assertRaises(ValueError):c.validate_stats(s,3)
        s=self.stats();s['timings_ms']['export']['sum']=float('nan')
        with self.assertRaises(ValueError):c.validate_stats(s,3)
        with self.assertRaises(ValueError):c.validate_stats(self.stats(),0)

    def test_fresh_alias_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'a').mkdir();(root/'a'/'f').write_text('x')
            with self.assertRaises(FileExistsError):fresh(root/'a')
            (root/'link').symlink_to(root/'a',target_is_directory=True)
            with self.assertRaises(ValueError):fresh(root/'link'/'new')
            with self.assertRaises(ValueError):c.regular(root,'link/f')
            with self.assertRaises(ValueError):c.regular(root,'../other')

    def test_unreviewed_inventory_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            inventory=Path(tmp)/'inventory.json';inventory.write_text('[]')
            with self.assertRaisesRegex(ValueError,'inventory hash'):c.read_bound('/missing',inventory)

    def test_hidden_rgb_and_alpha(self):
        import numpy as np
        from vtx.gfx_render import png_encode_rgba
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a.png';b=Path(tmp)/'b.png'
            pixels=np.zeros((2,2,4),dtype=np.uint8);a.write_bytes(png_encode_rgba(pixels))
            b.write_bytes(a.read_bytes());self.assertTrue(q.png_comparison(a,b,2,2)['equal'])
            pixels[0,0,0]=1;b.write_bytes(png_encode_rgba(pixels))
            out=q.png_comparison(a,b,2,2);self.assertFalse(out['equal']);self.assertEqual(out['different_alpha_pixels'],0)
            pixels[0,0,3]=1;b.write_bytes(png_encode_rgba(pixels))
            self.assertEqual(q.png_comparison(a,b,2,2)['different_alpha_pixels'],1)

    def test_production_n1_branch_and_exact_sink_edits(self):
        import numpy as np
        from vtx.gfx_render import png_encode_rgba
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);seqs={}
            for arm,entry in [('baseline','T08.01'),('baseline','T07.01'),('candidate','T08_T07')]:
                p=root/arm/entry;p.mkdir(parents=True)
                for i in range(3):(p/f'f{i:04d}.png').write_bytes(png_encode_rgba(np.zeros((2,2,4),dtype=np.uint8)))
                seqs[(arm,entry)]=p
            for bg in ('transparent','black','white'):
                commands=q.dense_n1_commands(seqs,root,bg,3,2,2);self.assertEqual(len(commands),2)
                for item in commands:
                    self.assertIn("overlay=x='if(between(t,",item['original_filtergraph'])
                    self.assertIn('setpts=PTS+0[',item['original_filtergraph'])
                    self.assertIn(':format=auto:',item['filtergraph'])
                    self.assertEqual(item['argv'][item['argv'].index('-frames:v')+1],'3')
                    self.assertEqual(item['argv'][item['argv'].index('-c:v')+1],'png')
                    self.assertEqual(item['original_n1_argv'][item['original_n1_argv'].index('-c:v')+1],'libx264')
            with self.assertRaises(ValueError):q.dense_n1_commands(seqs,root,'paper-imitation',3,2,2)

    def test_never_fill_missing_active_frame(self):
        with patch.object(q,'frame_index',return_value={}):
            with self.assertRaisesRegex(ValueError,'coverage'):
                q.dense_sequences([{},{}],Path('/unused'),targets=[0,18],width=2,height=2)


class GroupStateTests(unittest.TestCase):
    def state(self,f):
        return {'status':'RENDERED_UNVERIFIED','globalFrame':f,'query':'?f='+str(f),'ready':'complete',
                'failureHidden':True,'members':[
                {'id':id_,'active':str(f>=start).lower(),'global':str(f),
                 'local':str(f-start) if f>=start else None,
                 'status':'scene-implemented-unverified' if f>=start else None}
                for id_,start in [('T08',18),('T07',0)]]}

    def test_group_ready_global_local_and_inactive(self):
        from experiments.render_pilot_runner.group_gate import check_state
        for f in (0,17,18,1380,3599):self.assertEqual(check_state(self.state(f),'file:///group.html?f='+str(f),{f}),f)

    def test_group_error_stale_visible_and_false_member(self):
        from experiments.render_pilot_runner.group_gate import check_state
        for key,value in [('status','ERROR'),('globalFrame',17),('failureHidden',False),('query','?f=17'),('ready','loading')]:
            state=self.state(18);state[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):check_state(state,'file:///g?f=18',{18})
        state=self.state(17);state['members'][0]['local']='0'
        with self.assertRaises(ValueError):check_state(state,'file:///g?f=17',{17})
        with self.assertRaises(ValueError):check_state(self.state(18),'file:///g?f=18&f=18',{18})

class WorkerTests(unittest.TestCase):
    def test_real_renderer_call_shape_and_failed_attempt_retention(self):
        import contextlib,json,os,sys,time,types
        from experiments.render_pilot_runner import runner,cleanup
        from vtx import gfx_render,gfx_environment,proc_supervisor,gfx_cli
        class Trace:
            def assert_valid(self,stats,expected_frames):pass
            def report(self):return {'test':'mock transport; no browser'}
        @contextlib.contextmanager
        def scope(**kwargs):yield Trace()
        @contextlib.contextmanager
        def clean_scope(gfx,trace,observations,path,**kwargs):
            yield
            observations.append({'status':'PASS','fixture':'mock renderer has no real browser'})
        module=types.ModuleType('experiments.fast_png_pilot.adapter');module.capture_scope=scope
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'baseline').mkdir()
                job={'arm':'baseline','batch':'continuous','entry':{'entryId':'T08.01','src':'wing.html','duration':3582},
                     'project':str(root),'mapping':[{'global':18,'local':0}],'expected_rendered':1}
                spec={'plan':{'mode':'fast-png','jobs':[job]},'output':str(root),'chrome':'unused',
                      'implementation':{'commit':'a'*40,'tree':'b'*40},'deadline_monotonic':c.clock_now()+10,'clock_kind':c.CLOCK_KIND}
                path=root/'spec.json';path.write_text(json.dumps(spec))
                def render(html,out,n,**kwargs):
                    if not fail:(Path(out)/'f0000.png').write_bytes(b'mock')
                    stats=ContractTests().stats();stats['rendered']=0 if fail else 1
                    stats['path_counts']['override']=0 if fail else 1;stats['failed']=[0] if fail else []
                    return stats
                with patch.dict(sys.modules,{'experiments.fast_png_pilot.adapter':module}),patch.dict(os.environ,c.PROFILE),\
                     patch.object(runner,'verify_plan'),patch.object(runner,'git_identity'),\
                     patch.object(proc_supervisor,'supervision_available',return_value=True),\
                     patch.object(gfx_environment,'collect',return_value={'status':'input-bound'}),\
                     patch.object(gfx_cli,'png_invalid_reason',return_value=None),\
                     patch.object(cleanup,'browser_cleanup_scope',clean_scope),\
                     patch.object(gfx_render,'render_page',side_effect=render) as invoked:
                    code=runner.render_arm(path,'baseline')
                self.assertEqual(code,1 if fail else 0)
                self.assertEqual(invoked.call_args.kwargs['frames'],[0])
                self.assertEqual(invoked.call_args.kwargs['tabs'],1)
                self.assertEqual(invoked.call_args.kwargs['width'],1920)
                self.assertEqual(invoked.call_args.kwargs['height'],1080)
                report=json.loads((root/'baseline'/'result.json').read_bytes())
                self.assertEqual(report['attempts'][0]['raw_stats']['failed'],[0] if fail else [])
                self.assertEqual(report['attempts'][0]['exit_code'],1 if fail else 0)

class FailureBoundaryTests(unittest.TestCase):
    def test_trns_comparison_is_rejected(self):
        import struct,zlib
        def chunk(kind,payload):
            return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))
        png+=chunk(b'tRNS',struct.pack('>HHH',0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'+bytes(6)+b'\0'+bytes(6)))+chunk(b'IEND',b'')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'transparent.png';p.write_bytes(png)
            with self.assertRaisesRegex(ValueError,'Unsupported PNG variant'):q.png_comparison(p,p,2,2)

    def test_pair_deadline_stops_before_second_arm(self):
        import json,subprocess,types
        from experiments.render_pilot_runner import runner
        from vtx import proc_supervisor
        groups=[]
        class Process:
            returncode=None
            def wait(self,timeout):raise subprocess.TimeoutExpired('mock child',timeout)
        class Supervisor:
            def __init__(self,*args,**kwargs):self.proc=Process();groups.append(self)
            def __enter__(self):return self
            def __exit__(self,*args):self.proc.returncode=-15
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);chrome=tmp/'chrome';chrome.write_text('fixture')
            plan={'base':{'commit':c.BASE_COMMIT,'tree':c.BASE_TREE},'bound':{'root':str(tmp/'bound')}}
            with patch.object(runner.sys,'platform','darwin'),patch.object(runner,'verify_plan'),\
                 patch.object(runner,'git_identity',return_value={'commit':'a'*40,'tree':'b'*40}),\
                 patch.object(runner,'clock_probe',return_value={'status':'MOCK_CLOCK'}),\
                 patch.object(proc_supervisor,'supervision_available',return_value=True),\
                 patch.object(proc_supervisor,'SupervisedProcessGroup',Supervisor),\
                 patch.object(runner.subprocess,'run',return_value=types.SimpleNamespace(stdout='')):
                code=runner.execute_pair(plan,tmp/'new',chrome,'a'*40,'b'*40)
            self.assertEqual(code,1);self.assertEqual(len(groups),1)
            result=json.loads((tmp/'new'/'result.json').read_bytes())
            self.assertTrue(result['processes'][0]['deadline_expired'])
            self.assertEqual(result['processes'][0]['returncode_after_cleanup'],-15)
            self.assertFalse((tmp/'new'/'candidate').exists())
            self.assertEqual(result['status'],'FAILED')

if __name__=='__main__':unittest.main()
