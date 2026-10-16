"""Tiny/offline harness contract tests: no original media/network/render required."""
import importlib.util
import json
import os
import sys
import time
import signal
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'issue85_real_media.py'
spec = importlib.util.spec_from_file_location('issue85_real_media', SCRIPT)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def metadata(**updates):
    v = dict(codec_type='video', width=3840, height=2160,
             r_frame_rate='30/1', avg_frame_rate='30/1', duration='20.000000',
             nb_frames='600', codec_name='prores', pix_fmt='yuv422p10le')
    v.update(updates)
    return {'streams':[v]}


class Qualification(unittest.TestCase):
    def test_native_metadata(self):
        self.assertEqual(h.qualify(metadata())['frames'],600)

    def test_unqualified_metadata(self):
        for delta in ({'r_frame_rate':'24/1'}, {'avg_frame_rate':'30000/1001'},
                      {'width':1920}, {'duration':'nan'}, {'duration':'19.9'},
                      {'nb_frames':'599'}, {'duration':'61'}):
            with self.subTest(delta=delta), self.assertRaises(h.Blocked):
                h.qualify(metadata(**delta))

    def test_unexpected_audio_blocks_for_rights(self):
        value = metadata()
        value['streams'].append({'codec_type':'audio'})
        with self.assertRaises(h.Blocked):
            h.qualify(value)

    def test_atoms_preserve_offsets_without_payload(self):
        atom = lambda name, body: struct.pack('>I4s',8+len(body),name)+body
        data = atom(b'ftyp',b'qt  ')+atom(b'mdat',b'payload'*8)+atom(b'moov',b'metadata')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'meta.mov'
            rows = h.metadata_file({'bytes':len(data)},path,lambda s,n:data[s:s+n])
            self.assertEqual([r['type'] for r in rows],['ftyp','mdat','moov'])
            saved = path.read_bytes()
            self.assertTrue(saved.endswith(b'metadata'))
            self.assertNotEqual(saved,data)
            self.assertEqual(len(saved),len(data))

    def test_malformed_atom_blocks(self):
        data=struct.pack('>I4s',4,b'moov')+b'0000'
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(h.Blocked):
            h.metadata_file({'bytes':len(data)},Path(directory)/'meta',lambda s,n:data[s:s+n])

    def test_range_budget_before_network(self):
        ranges=h.Ranges({'bytes':h.MAX_BYTES,'etag':'"strong"'})
        with patch.object(h,'request') as request, self.assertRaises(h.Blocked):
            ranges.get(0,h.META_CAP+1)
        request.assert_not_called()

    def test_range_200_never_reads_body(self):
        class Response:
            status=200
            headers={}
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args): raise AssertionError('Must not read full body')
        with patch.object(h,'request',return_value=Response()), self.assertRaises(h.Blocked):
            h.Ranges({'bytes':h.MIN_BYTES,'etag':'"x"'}).get(0,16)

    def test_original_size_gate(self):
        class Response:
            status=200
            headers={'Content-Length':'999999999','Content-Type':'video/quicktime','ETag':'"x"'}
            def __enter__(self): return self
            def __exit__(self,*args): pass
        with patch.object(h,'request',return_value=Response()), self.assertRaises(h.Blocked):
            h.head()

    def test_no_etag_is_not_a_qualification_failure(self):
        class Response:
            status=200
            url=h.URL
            headers={'Content-Length':str(h.MIN_BYTES),'Content-Type':'video/quicktime'}
            def __enter__(self): return self
            def __exit__(self,*args): pass
        with patch.object(h,'request',return_value=Response()):
            self.assertIsNone(h.head()['etag'])
        Response.headers['ETag']='W/"weak"'
        with patch.object(h,'request',return_value=Response()):
            self.assertIsNone(h.head()['etag'])

    def test_redirect_is_blocked(self):
        with self.assertRaises(h.Blocked):
            h.NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid/video')

    def test_object_change_range(self):
        class Response:
            status=206
            headers={'Content-Range':f'bytes 0-15/{h.MIN_BYTES}', 'ETag':'"changed"'}
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args): raise AssertionError('Changed body must not be read')
        with patch.object(h,'request',return_value=Response()), self.assertRaises(h.Blocked):
            h.Ranges({'bytes':h.MIN_BYTES,'etag':'"original"'}).get(0,16)

    def test_acquisition_checks_delayed_eof(self):
        now=[0]
        class Response:
            status=200
            headers={'Content-Length':'3'}
            calls=0
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n):
                self.calls+=1
                now[0]=1 if self.calls==1 else 1301
                return b'abc' if self.calls==1 else b''
        with tempfile.TemporaryDirectory() as directory, patch.object(h,'request',return_value=Response()), patch.object(h.time,'monotonic',side_effect=lambda:now[0]):
            with self.assertRaisesRegex(h.Blocked,'deadline'):
                h.acquire({'bytes':3,'etag':None},Path(directory)/'tiny')
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux wall-clock HTTP alarm')
    def test_blocking_read_is_interrupted(self):
        class Response:
            status=206
            headers={'Content-Range':'bytes 0-2/3'}
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n):
                time.sleep(2)
                return b'abc'
        previous=signal.getsignal(signal.SIGALRM)
        with patch.object(h,'METADATA_SECONDS',.05), patch.object(h,'request',return_value=Response()):
            ranges=h.Ranges({'bytes':3,'etag':None})
            started=time.monotonic()
            with self.assertRaisesRegex(h.Blocked,'deadline'):
                ranges.get(0,3)
            self.assertLess(time.monotonic()-started,.5)
        self.assertIs(signal.getsignal(signal.SIGALRM),previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux wall-clock HTTP alarm')
    def test_acquisition_blocking_eof_is_interrupted(self):
        class Response:
            status=200
            headers={'Content-Length':'3'}
            calls=0
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n):
                self.calls+=1
                if self.calls==1: return b'abc'
                time.sleep(2)
                return b''
        with tempfile.TemporaryDirectory() as directory, patch.object(h,'ACQUIRE_SECONDS',.05), patch.object(h,'request',return_value=Response()):
            started=time.monotonic()
            with self.assertRaisesRegex(h.Blocked,'deadline'):
                h.acquire({'bytes':3,'etag':None},Path(directory)/'tiny')
            self.assertLess(time.monotonic()-started,.5)

    def test_range_terminal_deadline(self):
        now=[0]
        class Response:
            status=206
            headers={'Content-Range':'bytes 0-2/3'}
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): now[0]=181; return b'abc'
        with patch.object(h.time,'monotonic',side_effect=lambda:now[0]), patch.object(h,'request',return_value=Response()):
            ranges=h.Ranges({'bytes':3,'etag':None})
            with self.assertRaisesRegex(h.Blocked,'deadline'):
                ranges.get(0,3)

    def test_streaming_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'tiny'; path.write_bytes(b'abc')
            self.assertEqual(h.sha(path),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')


class RunnerContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runner = SCRIPT.with_name('issue85_media_runner.py')
        spec = importlib.util.spec_from_file_location('issue85_media_runner', runner)
        cls.r = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.r)

    def test_real_small_timeline(self):
        value=self.r.timeline_spec()
        self.assertEqual(value['project'], {'fps':30,'width':1920,'height':1080,'duration_frames':360})
        self.assertEqual([c['src_in'] for c in value['video_tracks'][0]['clips']],[0,240,480])
        self.assertEqual([c['src_in'] for c in value['audio_tracks'][0]['events']],[0,240,480])
        self.assertEqual(self.r.PAIR_ORDER,(('baseline','candidate'),('candidate','baseline'),('baseline','candidate')))

    def test_timeline_is_accepted_by_real_ctx(self):
        from vtx.infra import Ctx
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'source.mp4').write_bytes(b'tiny-source')
            timeline=root/'timeline.json'
            timeline.write_text(json.dumps(self.r.timeline_spec()))
            context=Ctx(timeline)
            self.assertEqual((context.fps,context.w,context.h,context.dur),(30,1920,1080,360))

    def test_mutation_restored_even_on_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'tiny'; path.write_bytes(b'x'*150000)
            before=path.stat()
            with self.assertRaisesRegex(RuntimeError,'test'):
                with self.r.middle_byte_mutation(path,outside_samples=True):
                    self.assertNotEqual(path.read_bytes(),b'x'*150000)
                    self.assertEqual(path.stat().st_mtime_ns,before.st_mtime_ns)
                    raise RuntimeError('test')
            self.assertEqual(path.read_bytes(),b'x'*150000)
            self.assertEqual(path.stat().st_mtime_ns,before.st_mtime_ns)

    def test_digest_interval_union(self):
        rows=[{'monotonic_start':0,'monotonic_end':2},
              {'monotonic_start':1,'monotonic_end':3},
              {'monotonic_start':5,'monotonic_end':6}]
        self.assertEqual(self.r.interval_union_seconds(rows),4)

    def test_digest_meter_counts_current_production_reads_and_eof(self):
        import hashlib
        from vtx import cache_state
        original_read, original_digest = os.read, cache_state.file_digest
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tiny'
            payload = b'x' * (1024 * 1024 + 17)
            path.write_bytes(payload)
            empty = Path(directory) / 'empty'; empty.write_bytes(b'')
            meter = self.r.DigestMeter()
            meter.install()
            try:
                self.assertEqual(cache_state.file_digest(path), hashlib.sha256(payload).hexdigest())
                self.assertEqual(cache_state.file_digest(empty), hashlib.sha256(b'').hexdigest())
                self.assertEqual([(row['bytes_read'], row['read_calls'], row['completed'])
                                  for row in meter.rows], [(len(payload), 3, True), (0, 1, True)])
                self.assertEqual(meter.summary()['file_digest_bytes'], len(payload))
            finally:
                meter.restore()
        self.assertIs(os.read, original_read)
        self.assertIs(cache_state.file_digest, original_digest)

    def test_digest_meter_preserves_legacy_stream_read_accounting(self):
        import hashlib
        from vtx import cache_state, recovery, hygiene
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tiny'; path.write_bytes(b'abc')
            meter = self.r.DigestMeter()
            meter.install()
            try:
                self.assertEqual(recovery._digest(path), hashlib.sha256(b'abc').hexdigest())
                self.assertEqual(hygiene._md5_file(path), hashlib.md5(b'abc').hexdigest())
                self.assertTrue(cache_state.media_stat_digest(path).endswith(
                    hashlib.sha1(b'abc').hexdigest()[:16]))
                self.assertEqual([(row['bytes_read'], row['read_calls']) for row in meter.rows],
                                 [(3, 2), (3, 2), (3, 1)])
                self.assertEqual(meter.summary()['all_instrumented_digest_bytes'], 9)
            finally:
                meter.restore()

    def test_digest_meter_os_read_error_keeps_partial_bytes_and_restores(self):
        import builtins
        from vtx import cache_state
        original_read, original_open = os.read, Path.open
        original_builtin_open, original_digest = builtins.open, cache_state.file_digest
        failure = OSError('read failed')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tiny'; path.write_bytes(b'abcdef')
            calls = []
            def failing_read(fd, size):
                calls.append((fd, size))
                if len(calls) == 1:
                    return original_read(fd, 3)
                raise failure
            with patch.object(os, 'read', side_effect=failing_read) as delegate:
                meter = self.r.DigestMeter()
                meter.install()
                try:
                    with self.assertRaises(OSError) as caught:
                        cache_state.file_digest(path)
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(len(calls), 2)
                    self.assertEqual([(row['bytes_read'], row['read_calls'], row['completed'])
                                      for row in meter.rows], [(3, 1, False)])
                    self.assertIsNone(getattr(meter.local, 'row', None))
                finally:
                    meter.restore()
                self.assertIs(os.read, delegate)
                self.assertIs(Path.open, original_open)
                self.assertIs(builtins.open, original_builtin_open)
                self.assertIs(cache_state.file_digest, original_digest)
            self.assertIs(os.read, original_read)
            cache_state.file_digest(path)
            self.assertEqual(len(meter.rows), 1)

    def test_digest_meter_excludes_unscoped_and_other_thread_os_reads(self):
        import threading
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tiny'; path.write_bytes(b'abcdef')
            def read_three():
                fd = os.open(path, os.O_RDONLY)
                try:
                    return os.read(fd, 3)
                finally:
                    os.close(fd)
            def digest(_path):
                values = []
                worker = threading.Thread(target=lambda: values.append(read_three()))
                worker.start(); worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(values, [b'abc'])
                return read_three()
            module = SimpleNamespace(__name__='digest_fixture', digest=digest)
            meter = self.r.DigestMeter()
            meter.install()
            meter.patch(module, 'digest')
            try:
                self.assertEqual(read_three(), b'abc')
                self.assertEqual(meter.rows, [])
                self.assertEqual(module.digest(path), b'abc')
                self.assertEqual(read_three(), b'abc')
                self.assertEqual([(row['bytes_read'], row['read_calls'], row['completed'])
                                  for row in meter.rows], [(3, 1, True)])
            finally:
                meter.restore()
            self.assertIs(module.digest, digest)

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux resource monitor')
    def test_owned_tiny_process_and_budget_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            result=self.r.monitored_run([sys.executable,'-c','print("tiny")'],root,
                                       dict(os.environ),root/'child.log',5,root)
            self.assertEqual(result['exit_code'],0)
            self.assertFalse(result['timed_out'])
            self.assertEqual((root/'child.log').read_text().strip(),'tiny')
            with patch.object(self.r,'allocated_bytes',return_value=self.r.SCRATCH_LIMIT_BYTES+1):
                result=self.r.monitored_run([sys.executable,'-c','raise Exception()'],root,
                                           dict(os.environ),root/'blocked.log',5,root)
            self.assertFalse(result['started'])
            self.assertEqual(result['budget_exceeded'],'scratch')
            self.assertFalse((root/'blocked.log').exists())

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux owned descendant monitor')
    def test_leader_exit_does_not_release_descendant_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            code="import subprocess,sys; subprocess.Popen([sys.executable,'-c',\"import time,pathlib; time.sleep(1); pathlib.Path('late').write_text('wrong')\"])"
            result=self.r.monitored_run([sys.executable,'-c',code],root,dict(os.environ),root/'log',.4,root)
            self.assertTrue(result['timed_out'])
            self.assertTrue(result['descendants_after_leader_exit'])
            self.assertTrue(result['owned_tree_finished'])
            self.assertGreaterEqual(result['owned_processes_observed'],2)
            self.assertTrue(result['adopted_children_reaped'])
            self.assertFalse((root/'late').exists())
            self.assertFalse(any(r['parent']==os.getpid() for r in self.r.process_records().values()))

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux owned descendant monitor')
    def test_short_descendant_finishes_before_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            code="import subprocess,sys; subprocess.Popen([sys.executable,'-c',\"import time,pathlib; time.sleep(.25); pathlib.Path('done').write_text('yes')\"])"
            result=self.r.monitored_run([sys.executable,'-c',code],root,dict(os.environ),root/'log',3,root)
            self.assertEqual(result['exit_code'],0)
            self.assertFalse(result['timed_out'])
            self.assertTrue(result['owned_tree_finished'])
            self.assertTrue((root/'done').exists())
            self.assertGreaterEqual(result['wall_seconds'],.25)

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux adopted descendant ownership')
    def test_new_session_descendant_is_owned_after_leader_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            code="import subprocess,sys; subprocess.Popen([sys.executable,'-c',\"import time,pathlib; time.sleep(1); pathlib.Path('late').write_text('wrong')\"],start_new_session=True)"
            result=self.r.monitored_run([sys.executable,'-c',code],root,dict(os.environ),root/'log',.4,root)
            self.assertTrue(result['timed_out'])
            self.assertTrue(result['owned_tree_finished'])
            self.assertTrue(result['adopted_children_reaped'])
            self.assertFalse((root/'late').exists())

    def cache_fixture(self):
        identity={'timeline_sha256':'a'*64,'config_sha256':'b'*64,
                  'sources_sha256':{'source':'c'*64},'final_sha256':'d'*64}
        stat={'size':123,'mtime_ns':1}
        paths=['cache/seg/a.mp4','cache/seg/b.mp4','cache/seg/c.mp4','cache/vseg/a.mp4']
        calls=[{'kind':'audio','source':'source','path':'cache/audio/a.wav','hit':True},
               {'kind':'cache_event','validator':'audio_cache_valid'},
               *[{'kind':'video','path':path,'hit':True} for path in paths],
               {'kind':'qc_record','hit':True},
               {'kind':'loudness_lookup','path':'out/final.mp4','hit':True},
               {'kind':'command','target':'out/.final.tmp.mp4','argv':['ffmpeg']}]
        for call in calls: call['completed']=True
        names=[f"底轨切点帧比对 dst={cl['dst_in']} src=source:{cl['src_in']+2}"
               for cl in self.r.timeline_spec()['video_tracks'][0]['clips']]
        names.append('空窗无叠加泄漏 @frame 359')
        child={'cache_observations':{'schema':'framivra.issue85.cache-observations.v1',
                 'final_before':stat,'final_after':stat,'identity_before':identity,
                 'identity_after':identity,'calls':calls,'always_recomputed':['mux']},
               'qc_report':{'checks':[{'name':name,'pass':True} for name in names],
                            'skipped_probes':[{'name':name} for name in names]}}
        return json.loads(json.dumps(child)),json.loads(json.dumps(child))

    def test_warm_cache_requires_each_applicable_hit_and_no_rebuild(self):
        cold,warm=self.cache_fixture()
        self.assertTrue(self.r.warm_cache_assessment(cold,warm,self.r.CANDIDATE)['passed'])
        for index,call in enumerate(warm['cache_observations']['calls']):
            if 'hit' not in call: continue
            changed=json.loads(json.dumps(warm))
            changed['cache_observations']['calls'][index]['hit']=False
            with self.subTest(kind=call['kind'],index=index):
                self.assertFalse(self.r.warm_cache_assessment(cold,changed,self.r.CANDIDATE)['passed'])
        for target in ('cache/audio/a.wav','cache/seg/a.mp4','cache/vseg/a.mp4'):
            changed=json.loads(json.dumps(warm))
            changed['cache_observations']['calls'].append(
                {'kind':'command','target':target,'completed':True})
            with self.subTest(rebuild=target):
                self.assertFalse(self.r.warm_cache_assessment(cold,changed,self.r.CANDIDATE)['passed'])
        for call in warm['cache_observations']['calls']:
            if 'hit' in call: call['hit']=False
        self.assertFalse(self.r.warm_cache_assessment(cold,warm,self.r.CANDIDATE)['passed'])

    def test_warm_remux_exceptions_require_full_evidence(self):
        cold,warm=self.cache_fixture()
        wm=warm['cache_observations']
        wm['final_after']['mtime_ns']=2
        for row in wm['calls']:
            if row['kind'] in ('qc_record','loudness_lookup'): row['hit']=False
        wm['calls'] += [{'kind':'command','target':'cache/qc_probe_x.raw','completed':True},
                       {'kind':'loudness_measure','path':'out/final.mp4','completed':True}]
        warm['qc_report']['skipped_probes']=[]
        self.assertTrue(self.r.warm_cache_assessment(cold,warm,self.r.BASELINE)['passed'])
        self.assertFalse(self.r.warm_cache_assessment(cold,warm,self.r.CANDIDATE)['passed'])
        wm['identity_after']['final_sha256']='e'*64
        self.assertTrue(self.r.warm_cache_assessment(cold,warm,self.r.CANDIDATE)['passed'])
        wm['calls']=[row for row in wm['calls'] if row.get('target')!='out/.final.tmp.mp4']
        self.assertFalse(self.r.warm_cache_assessment(cold,warm,self.r.CANDIDATE)['passed'])

    def test_orchestrator_rejects_zero_warm_hit_evidence(self):
        r=self.r
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source'; source.write_bytes(b'x'*150000)
            seen=[]
            def fake_run(argv,cwd,env,log,timeout,budget_root):
                evidence=Path(argv[-3]); seen.append(evidence.name)
                r.write_json(evidence/'result.json',{'passed':True,'render_wall_seconds':100,
                    'decoded_identity':{'v':'same','a':'same'},'cache_hits':0})
                return {'exit_code':0,'timed_out':False,'budget_exceeded':None}
            with patch.object(r,'resolve_candidate',return_value={'commit':r.CANDIDATE, 'tree':r.CANDIDATE_TREE, 'baseline':r.BASELINE, 'head':'f'*40, 'chain':[]}), patch.object(r,'checked_revision',side_effect=lambda p,e:{'path':str(p),'commit':e}), patch.object(r,'monitored_run',side_effect=fake_run):
                result=r.run_experiment(root,root,source,root/'runs')
            self.assertIn('warm',seen)
            self.assertEqual(result['status'],'FAIL')
            warm=[sample for sample in result['samples'] if sample['phase']=='warm']
            self.assertTrue(warm)
            self.assertFalse(warm[0]['warm_cache_gate']['passed'])

    def test_each_of_six_warm_samples_is_independently_gated(self):
        r=self.r
        for rejected in [None,*[(pair,label) for pair in (1,2,3) for label in ('baseline','candidate')]]:
            with self.subTest(rejected=rejected), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); source=root/'source'; source.write_bytes(b'x'*150000)
                def fake_run(argv,cwd,env,log,timeout,budget_root):
                    evidence=Path(argv[-3]); action=argv[-2]
                    cold,warm=self.cache_fixture()
                    label=Path(cwd).name.split('-')[-1]
                    pair=int(Path(cwd).name.split('-')[1])
                    child=warm if evidence.name=='warm' else cold
                    if evidence.name=='warm' and label=='baseline':
                        wm=child['cache_observations']; wm['final_after']['mtime_ns']=2
                        for row in wm['calls']:
                            if row['kind'] in ('qc_record','loudness_lookup'): row['hit']=False
                        wm['calls'] += [{'kind':'command','target':'cache/qc_probe_x.raw','completed':True},
                            {'kind':'loudness_measure','path':'out/final.mp4','completed':True}]
                        child['qc_report']['skipped_probes']=[]
                    if evidence.name=='warm' and (pair,label)==rejected:
                        child['cache_observations']['calls'][0]['hit']=False
                    child.update(passed=True,render_wall_seconds=100,decoded_identity={'v':'same','a':'same'})
                    if action=='negative': child['negative']={'passed':True}
                    r.write_json(evidence/'result.json',child)
                    return {'exit_code':0,'timed_out':False,'budget_exceeded':None}
                with patch.object(r,'resolve_candidate',return_value={'commit':r.CANDIDATE, 'tree':r.CANDIDATE_TREE, 'baseline':r.BASELINE, 'head':'f'*40, 'chain':[]}), patch.object(r,'checked_revision',side_effect=lambda p,e:{'path':str(p),'commit':e}), patch.object(r,'monitored_run',side_effect=fake_run):
                    result=r.run_experiment(root,root,source,root/'runs')
                self.assertEqual(result['status'],'PASS' if rejected is None else 'FAIL')
                if rejected is None:
                    self.assertEqual(sum(sample['phase']=='warm' for sample in result['samples']),6)
                else:
                    bad=[sample for sample in result['samples'] if sample['phase']=='warm' and
                         (sample['pair'],sample['label'])==rejected]
                    self.assertEqual(len(bad),1)
                    self.assertFalse(bad[0]['warm_cache_gate']['passed'])

    def warm_cache_fixture(self, baseline=False, changed_content=False):
        import copy
        r = self.r
        row = lambda kind, **kw: {'kind': kind, 'completed': True, **kw}
        ident = {'timeline_sha256': 'a' * 64, 'config_sha256': 'b' * 64,
                 'sources_sha256': {'source': 'c' * 64}, 'final_sha256': 'd' * 64}
        paths = [f'cache/seg/{i}.mp4' for i in range(3)] + ['cache/vseg/segment.mp4']
        names = [f'底轨切点帧比对 dst={d} src=source:{s + 2}'
                 for d, s in ((0, 0), (120, 240), (240, 480))] + ['空窗无叠加泄漏 @frame 359']
        cold = {'cache_observations': {'schema': 'framivra.issue85.cache-observations.v1',
                    'identity_before': dict(ident, final_sha256=None), 'identity_after': ident,
                    'final_before': None, 'final_after': {'size': 999, 'mtime_ns': 1},
                    'always_recomputed': [],
                    'calls': [row('audio', source='source', path='cache/audio/source.wav', hit=False)]
                             + [row('video', path=p, hit=False) for p in paths]},
                'qc_report': {'checks': [{'name': name, 'pass': True} for name in names],
                              'skipped_probes': []}}
        warm = copy.deepcopy(cold)
        obs = warm['cache_observations']
        obs['identity_before'] = copy.deepcopy(ident)
        obs['final_before'] = dict(obs['final_after'])
        obs['final_after']['mtime_ns'] = 2
        if changed_content:
            obs['identity_after']['final_sha256'] = 'e' * 64
        misses = baseline or changed_content
        obs['calls'] = [row('audio', source='source', path='cache/audio/source.wav', hit=True),
                        row('cache_event', validator='audio_cache_valid')]
        obs['calls'] += [row('video', path=p, hit=True) for p in paths]
        obs['calls'] += [row('qc_record', hit=not misses),
                         row('loudness_lookup', path='out/final.mp4', hit=not misses),
                         row('command', target='out/.final.tmp.mp4', argv=['ffmpeg'])]
        if misses:
            obs['calls'] += [row('loudness_measure', path='out/final.mp4'),
                             row('command', target='cache/qc_probe_1.raw', argv=['ffmpeg'])]
        else:
            warm['qc_report']['skipped_probes'] = [{'name': name, 'seg': [0, 360]} for name in names]
        return cold, warm

    def test_warm_cache_real_applicability_contract(self):
        for baseline, changed in ((False, False), (True, False), (False, True)):
            with self.subTest(baseline=baseline, changed=changed):
                cold, warm = self.warm_cache_fixture(baseline, changed)
                result = self.r.warm_cache_assessment(cold, warm,
                    self.r.BASELINE if baseline else self.r.CANDIDATE)
                self.assertTrue(result['passed'], result)
                self.assertEqual(result['caches']['qc_probes']['applicable'], not (baseline or changed))

    def test_warm_cache_missing_each_hit_and_rebuild_fails(self):
        import copy
        cold, warm = self.warm_cache_fixture()
        for index, row in enumerate(warm['cache_observations']['calls']):
            if 'hit' not in row:
                continue
            with self.subTest(missing_hit=row):
                changed = copy.deepcopy(warm)
                changed['cache_observations']['calls'][index]['hit'] = False
                self.assertFalse(self.r.warm_cache_assessment(cold, changed, self.r.CANDIDATE)['passed'])
        for target in ('cache/audio/source.wav', 'cache/seg/0.mp4', 'cache/vseg/segment.mp4',
                       'cache/vseg_pass/p0.mkv', 'cache/qc_probe_1.raw'):
            with self.subTest(rebuild=target):
                changed = copy.deepcopy(warm)
                changed['cache_observations']['calls'].append(
                    {'kind': 'command', 'completed': True, 'target': target, 'argv': ['ffmpeg']})
                self.assertFalse(self.r.warm_cache_assessment(cold, changed, self.r.CANDIDATE)['passed'])
        warm['cache_observations']['calls'].append(
            {'kind': 'loudness_measure', 'completed': True, 'path': 'out/final.mp4'})
        self.assertFalse(self.r.warm_cache_assessment(cold, warm, self.r.CANDIDATE)['passed'])

    def test_warm_cache_exceptions_need_real_evidence(self):
        for baseline in (True, False):
            cold, warm = self.warm_cache_fixture(baseline, changed_content=not baseline)
            revision = self.r.BASELINE if baseline else self.r.CANDIDATE
            warm['cache_observations']['calls'] = [row for row in warm['cache_observations']['calls']
                if row.get('target') != 'out/.final.tmp.mp4']
            self.assertFalse(self.r.warm_cache_assessment(cold, warm, revision)['passed'])
        cold, warm = self.warm_cache_fixture()
        warm['cache_observations']['identity_after']['sources_sha256']['source'] = 'f' * 64
        self.assertFalse(self.r.warm_cache_assessment(cold, warm, self.r.CANDIDATE)['passed'])
        self.assertFalse(self.r.warm_cache_assessment({}, {'cache_hits': 0}, self.r.CANDIDATE)['passed'])

    def test_cache_meter_delegates_existing_decisions_unchanged(self):
        from types import SimpleNamespace
        from vtx import stages, composite, events, recovery, qc, loudness_cache
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'out').mkdir()
            (root / 'source.mp4').write_bytes(b'tiny source')
            (root / 'out/final.mp4').write_bytes(b'tiny output')
            (root / 'timeline.json').write_text(json.dumps(self.r.timeline_spec()))
            (root / 'config.json').write_text('{}')
            with patch.object(stages, 'source_audio_extraction', return_value=(root / 'cache/audio/a.wav', ['real miss'])) as audio, \
                 patch.object(composite, 'media_cache_valid', return_value=False) as video, \
                 patch.object(events, 'cache_hit', return_value='event') as event, \
                 patch.object(recovery, 'atomic_ffmpeg', return_value='executed') as command, \
                 patch.object(qc, '_load_qc_pass_record', return_value=None) as qcr, \
                 patch.object(loudness_cache, 'lookup', return_value=None) as loud, \
                 patch.object(loudness_cache, 'measure_and_store', return_value={'i': -14}) as measure:
                meter = self.r.CacheMeter(root)
                meter.install()
                c = SimpleNamespace()
                try:
                    self.assertEqual(stages.source_audio_extraction(c, 'source')[1], ['real miss'])
                    self.assertFalse(composite.media_cache_valid(root / 'cache/seg/s.mp4', 120))
                    self.assertEqual(events.cache_hit(c, 'audio_cache_valid'), 'event')
                    self.assertEqual(recovery.atomic_ffmpeg(c, ['ffmpeg', str(root / 'out/.final.tmp.mp4')], None), 'executed')
                    self.assertIsNone(qc._load_qc_pass_record(c))
                    self.assertIsNone(loudness_cache.lookup(root / 'out/final.mp4', root=root))
                    self.assertEqual(loudness_cache.measure_and_store(root / 'out/final.mp4', None, root=root), {'i': -14})
                    observed = meter.summary()
                    self.assertTrue(all(row['completed'] for row in observed['calls']))
                    self.assertTrue(all(row['hit'] is False for row in observed['calls'] if 'hit' in row))
                    self.assertEqual(observed['identity_before'], observed['identity_after'])
                finally:
                    meter.restore()
                for original in (audio, video, event, command, qcr, loud, measure):
                    original.assert_called_once()
                self.assertIs(stages.source_audio_extraction, audio)

    def test_performance_never_selects_best_pair(self):
        def samples(deltas, warm_delta=0):
            return [{'label':label,'phase':phase,'pair':pair,
                     'child':{'render_wall_seconds':100+(0 if label=='baseline' else
                         (deltas[pair-1] if phase=='cold' else warm_delta))}}
                    for phase in ('cold','warm') for pair in (1,2,3)
                    for label in ('baseline','candidate')]
        self.assertEqual(self.r.performance_assessment(samples([5,5,5]))['status'],'PASS')
        self.assertEqual(self.r.performance_assessment(samples([5,61,5]))['status'],'FAIL')
        self.assertEqual(self.r.performance_assessment(samples([50,55,59]))['status'],'INCONCLUSIVE')
        self.assertEqual(self.r.performance_assessment(samples([5,5,5],90))['status'],'PASS')

    def test_output_equivalence_requires_every_sample(self):
        make=lambda x:{'child':{'decoded_identity':x}}
        self.assertTrue(self.r.compare_identities([make({'v':'same'}),make({'v':'same'})]))
        self.assertFalse(self.r.compare_identities([make({'v':'same'}),make({'v':'other'})]))
        self.assertFalse(self.r.compare_identities([make({'v':'same'}),{}]))
        self.assertFalse(self.r.compare_identities([]))


class RevisionResolution(unittest.TestCase):
    """Real tiny local Git graphs; no media, remotes or server metadata."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            'issue85_revision_contracts', SCRIPT.with_name('issue85_media_runner.py'))
        cls.r = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.r)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': temporary.name,
                    'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_CONFIG_GLOBAL': os.devnull,
                    'GIT_AUTHOR_NAME': 'Offline test', 'GIT_AUTHOR_EMAIL': 'test@example.invalid',
                    'GIT_COMMITTER_NAME': 'Offline test', 'GIT_COMMITTER_EMAIL': 'test@example.invalid'}
        self.git('init', '-q')
        self.paths = ('scripts/issue85_media_runner.py', 'scripts/issue85_real_media.py',
                      'tests/test_issue85_real_media.py', '.github/workflows/issue85-real-media.yml')
        for name in (*self.paths, 'vtx/tiny.py'):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('baseline\n')
        self.commits = [self.commit()]
        for name, content in (('vtx/tiny.py', 'initial\n'), ('vtx/tiny.py', 'candidate\n'),
                              (self.paths[0], 'harness one\n'), (self.paths[1], 'harness two\n'),
                              (self.paths[2], 'binding\n')):
            (self.repo / name).write_text(content)
            self.commits.append(self.commit())
        self.trees = [self.git('rev-parse', f'{commit}^{{tree}}') for commit in self.commits]
        pins = patch.multiple(self.r, BASELINE=self.commits[0], BASELINE_TREE=self.trees[0],
                              INITIAL_PRODUCTION_TREE=self.trees[1], CANDIDATE_TREE=self.trees[2],
                              REVIEWED_HARNESS_TREES=tuple(self.trees[3:5]))
        pins.start()
        self.addCleanup(pins.stop)

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.repo), *args], env=self.env,
                                capture_output=True, text=True, timeout=10, check=True)
        return result.stdout.strip()

    def commit(self):
        self.git('add', '--all')
        self.git('commit', '-q', '-m', 'tiny offline fixture')
        return self.git('rev-parse', 'HEAD')

    def synthetic_commit(self, tree, *parents):
        args = ['commit-tree', tree, '-m', 'synthetic offline fixture']
        for parent in parents:
            args += ['-p', parent]
        return self.git(*args)

    def checkout(self, commit):
        self.git('checkout', '-q', '--detach', commit)

    def test_resolves_actual_second_commit_without_commit_alias(self):
        with patch.object(self.r, 'CANDIDATE', 'e' * 40):
            result = self.r.resolve_candidate(self.repo)
        self.assertEqual(result['commit'], self.commits[2])
        self.assertEqual(result['tree'], self.trees[2])
        self.assertEqual(result['baseline'], self.commits[0])
        self.assertEqual(result['head'], self.commits[-1])
        self.assertEqual(len(result['chain']), 5)

    def test_changed_commit_metadata_preserves_verified_tree_sequence(self):
        rewritten = [self.commits[0]]
        for tree in self.trees[1:]:
            rewritten.append(self.synthetic_commit(tree, rewritten[-1]))
        self.assertNotEqual(rewritten[2], self.commits[2])
        self.checkout(rewritten[-1])
        result = self.r.resolve_candidate(self.repo)
        self.assertEqual(result['commit'], rewritten[2])
        self.assertEqual(result['head'], rewritten[-1])
        self.checkout(rewritten[2])
        self.assertEqual(self.r.checked_revision(self.repo, rewritten[2])['commit'], rewritten[2])

    def test_requires_binding_commit_after_four_frozen_commits(self):
        self.checkout(self.commits[4])
        with self.assertRaises(ValueError):
            self.r.resolve_candidate(self.repo)

    def test_refuses_absent_baseline(self):
        with patch.object(self.r, 'BASELINE', 'f' * 40), self.assertRaises(ValueError):
            self.r.resolve_candidate(self.repo)

    def test_checks_each_frozen_tree(self):
        wrong = 'f' * 40
        for changes in ({'BASELINE_TREE': wrong}, {'INITIAL_PRODUCTION_TREE': wrong},
                        {'CANDIDATE_TREE': wrong},
                        {'REVIEWED_HARNESS_TREES': (wrong, self.trees[4])},
                        {'REVIEWED_HARNESS_TREES': (self.trees[3], wrong)},
                        {'REVIEWED_HARNESS_TREES': (self.trees[4], self.trees[3])}):
            with self.subTest(changes=changes), patch.multiple(self.r, **changes):
                with self.assertRaises(ValueError):
                    self.r.resolve_candidate(self.repo)

    def test_rejects_merge_even_when_head_tree_is_unchanged(self):
        merge = self.synthetic_commit(self.trees[-1], self.commits[-1], self.commits[0])
        self.checkout(merge)
        with self.assertRaises(ValueError):
            self.r.resolve_candidate(self.repo)

    def test_rejects_second_candidate_tree(self):
        duplicate = self.synthetic_commit(self.trees[2], self.commits[-1])
        self.checkout(duplicate)
        with self.assertRaises(ValueError):
            self.r.resolve_candidate(self.repo)

    def test_rejects_out_of_scope_change_even_after_revert(self):
        (self.repo / 'README.md').write_text('unauthorized change\n')
        self.commit()
        (self.repo / 'README.md').unlink()
        self.commit()
        self.assertEqual(self.git('rev-parse', 'HEAD^{tree}'), self.trees[-1])
        with self.assertRaises(ValueError):
            self.r.resolve_candidate(self.repo)

    def test_rejects_removed_or_executable_harness_path(self):
        for executable in (False, True):
            with self.subTest(executable=executable):
                self.checkout(self.commits[-1])
                if executable:
                    (self.repo / self.paths[0]).chmod(0o755)
                else:
                    (self.repo / self.paths[0]).unlink()
                self.commit()
                self.assertEqual(self.git('diff', '--exit-code', 'HEAD', '--'), '')
                with self.assertRaisesRegex(ValueError, 'Harness paths require'):
                    self.r.resolve_candidate(self.repo)

    def test_rejects_symlink_or_submodule_harness_path(self):
        for kind in ('symlink', 'submodule'):
            with self.subTest(kind=kind):
                self.checkout(self.commits[-1])
                if kind == 'symlink':
                    path = self.repo / self.paths[0]
                    path.unlink()
                    path.symlink_to('../vtx/tiny.py')
                    self.commit()
                else:
                    self.git('update-index', '--cacheinfo', '160000',
                             self.commits[2], self.paths[0])
                    self.git('commit', '-q', '-m', 'changed harness to gitlink')
                    path = self.repo / self.paths[0]
                    path.unlink()
                    path.mkdir()  # Uninitialized gitlink, with a clean worktree.
                self.assertEqual(self.git('diff', '--exit-code', 'HEAD', '--'), '')
                with self.assertRaisesRegex(ValueError, 'Harness paths require'):
                    self.r.resolve_candidate(self.repo)

    def test_checked_revision_accepts_exact_baseline_and_candidate(self):
        for index in (0, 2):
            with self.subTest(index=index):
                self.checkout(self.commits[index])
                result = self.r.checked_revision(self.repo, self.commits[index])
                self.assertEqual(result['commit'], self.commits[index])

    def test_checked_revision_rejects_other_commit_and_symbolic_ref(self):
        self.checkout(self.commits[2])
        for expected in (self.commits[1], 'HEAD', self.commits[2][:12]):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.r.checked_revision(self.repo, expected)

    def test_checked_revision_rejects_wrong_candidate_or_parent_tree(self):
        cases = ((self.trees[1], self.commits[1]),
                 (self.trees[2], self.commits[0]))
        for tree, parent in cases:
            with self.subTest(tree=tree, parent=parent):
                commit = self.synthetic_commit(tree, parent)
                self.checkout(commit)
                with self.assertRaises(ValueError):
                    self.r.checked_revision(self.repo, commit)

    def test_checked_revision_rejects_unrelated_grandparent_with_same_tree(self):
        other_base = self.synthetic_commit(self.trees[0])
        self.assertNotEqual(other_base, self.commits[0])
        parent = self.synthetic_commit(self.trees[1], other_base)
        candidate = self.synthetic_commit(self.trees[2], parent)
        self.checkout(candidate)
        with self.assertRaises(ValueError):
            self.r.checked_revision(self.repo, candidate)

    def test_checked_revision_rejects_candidate_or_parent_merge(self):
        parent_merge = self.synthetic_commit(self.trees[1], self.commits[0], self.commits[-1])
        cases = (self.synthetic_commit(self.trees[2], self.commits[1], self.commits[0]),
                 self.synthetic_commit(self.trees[2], parent_merge))
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.checkout(candidate)
                with self.assertRaises(ValueError):
                    self.r.checked_revision(self.repo, candidate)

    def test_checked_revision_checks_baseline_tree(self):
        self.checkout(self.commits[0])
        with patch.object(self.r, 'BASELINE_TREE', self.trees[1]), self.assertRaises(ValueError):
            self.r.checked_revision(self.repo, self.commits[0])

    def test_checked_revision_still_rejects_dirty_and_untracked_production(self):
        self.checkout(self.commits[2])
        production = self.repo / 'vtx/tiny.py'
        production.write_text('dirty\n')
        with self.assertRaises(ValueError):
            self.r.checked_revision(self.repo, self.commits[2])
        self.git('checkout', '--', 'vtx/tiny.py')
        (self.repo / 'vtx/untracked.py').write_text('untracked\n')
        with self.assertRaises(ValueError):
            self.r.checked_revision(self.repo, self.commits[2])


if __name__ == '__main__':
    unittest.main()
