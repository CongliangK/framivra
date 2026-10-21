"""Normally initialized probe: no origin inference and complete parsing chains."""
from tests._support import temporary_root
import contextlib
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from vtx.infra import Ctx


class ProbeObservation(unittest.TestCase):
    def setUp(self):
        self.root = temporary_root(self); self.media = self.root/'media.wav'
        self.media.write_bytes(b'fixture')
        self.timeline = self.root/'timeline.json'
        self.timeline.write_text(json.dumps({'project': {'fps':30, 'width':160, 'height':90, 'duration_frames':90},
            'sources':[{'id':'s', 'path':'media.wav'}], 'video_tracks':[], 'audio_tracks':[], 'subtitles':[]}))

    def context(self, enabled=True):
        return Ctx(self.timeline, diagnostics=[] if enabled else None)

    def invoke(self, payload, packets='90'):
        c = self.context(); calls=[]
        def run(cmd):
            calls.append(cmd)
            return SimpleNamespace(stdout=packets if '-count_packets' in cmd else json.dumps(payload))
        c.run=run; error=None; result=None
        with contextlib.redirect_stderr(io.StringIO()):
            try: result=c.probe(self.media)
            except BaseException as caught: error=caught
        return c,result,error,calls

    @staticmethod
    def video(nb='90', rate='30/1', duration='3', **fields):
        return {'streams':[dict(codec_type='video', nb_frames=nb, r_frame_rate=rate, width=160, height=90, **fields)],
                'format':{'duration':duration}}

    def test_initialized_warm_probe_reads_no_timeline_fields(self):
        for enabled in (False,True):
            c=self.context(enabled); reads=[]
            class Traced(dict):
                def get(self,k,*args): reads.append(k); return super().get(k,*args)
                def __getitem__(self,k): reads.append(k); return super().__getitem__(k)
            c.tl=Traced(c.tl); c.tl['sources']=[Traced(c.tl['sources'][0])]
            reads.clear(); cached={'cached':True};c.probes[self.media]=cached
            c.run=lambda cmd:self.fail('warm probe invoked provider')
            self.assertIs(c.probe(self.media),cached)
            self.assertEqual(reads,[])

    def test_complete_terminal_video_conversion_chains(self):
        cases=[
            (self.video(rate='9'*400+'/1'), OverflowError, '$.streams[video].r_frame_rate'),
            (self.video(rate='bad',duration=10**400), OverflowError, '$.format.duration'),
            (self.video(nb='9'*400,rate='bad'), OverflowError, ['$.streams[video].nb_frames','$.format.duration']),
            (self.video(nb='9'*5000), ValueError, '$.streams[video].nb_frames'),
        ]
        for payload,kind,pointer in cases:
            c,_,error,calls=self.invoke(payload)
            self.assertIsInstance(error,kind); self.assertEqual(len(calls),1)
            self.assertTrue(getattr(error,'_vtx_diagnostic_handled',False))
            self.assertEqual(len(c.diagnostics),1)
            record=c.diagnostics[0]
            self.assertEqual(record['status'],'unknown')
            self.assertEqual(record['source']['resource'],str(self.media))
            self.assertEqual(record['source']['provider_path'],pointer)
            self.assertEqual(record['json_path'],'$')

    def test_recoverable_rate_and_duration_branches(self):
        for rate in ('bad','0/0','30/0','30/-1','9'*5000+'/1'):
            c,result,error,calls=self.invoke(self.video(rate=rate))
            self.assertIsNone(error); self.assertEqual(result['fps'],30)
            self.assertEqual(c.diagnostics,[]);self.assertEqual(len(calls),1)
        for duration in (None,0,'bad','NaN',{},[]):
            c,_,error,_=self.invoke(self.video(rate='bad',duration=duration))
            self.assertIsInstance(error,SystemExit)
            self.assertEqual([r['rule_id'] for r in c.diagnostics],['probe.fps'])
        payload=self.video();payload['format']=None
        c,result,error,_=self.invoke(payload)
        self.assertIsNone(error);self.assertEqual(result['fps'],30);self.assertEqual(c.diagnostics,[])
        payload['streams'][0]['r_frame_rate']='bad'
        c,_,error,_=self.invoke(payload)
        self.assertIsInstance(error,AttributeError)
        self.assertEqual(c.diagnostics[0]['source']['provider_path'],'$.format.duration')

    def test_packet_and_audio_terminal_chains(self):
        for nb in ('0','bad',''):
            c,result,error,calls=self.invoke(self.video(nb=nb))
            self.assertIsNone(error);self.assertEqual(result['nb_frames'],90)
            self.assertEqual(len(calls),2);self.assertEqual(c.diagnostics,[])
        c,_,error,calls=self.invoke(self.video(nb='0',rate='bad'),packets='9'*400)
        self.assertIsInstance(error,OverflowError);self.assertEqual(len(calls),2)
        self.assertEqual(c.diagnostics[0]['source']['provider_path'],['$.streams[video].nb_read_packets','$.format.duration'])
        for duration in (10**400,'inf','nan','bad'):
            c,_,error,calls=self.invoke({'streams':[{'codec_type':'audio'}],'format':{'duration':duration}})
            self.assertIsInstance(error,(ValueError,OverflowError));self.assertEqual(len(calls),1)
            self.assertEqual(c.diagnostics[0]['status'],'unknown')
            self.assertEqual(c.diagnostics[0]['source']['provider_path'],'$.format.duration')

if __name__=='__main__':unittest.main()
