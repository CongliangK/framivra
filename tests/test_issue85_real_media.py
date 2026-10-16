"""Tiny/offline harness contract tests: no original media/network/render required."""
import importlib.util
import json
import os
import sys
import struct
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


if __name__ == '__main__':
    unittest.main()
