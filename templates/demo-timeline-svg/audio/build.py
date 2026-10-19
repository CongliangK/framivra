#!/usr/bin/env python3
"""Build only the production premaster bed and reusable short cue.
Adapted from the reviewed 30-second audio-r1 score; no downloads or installs.
Run via ../build_demo.js --prepare; audition mastering is deliberately separate.
"""
from pathlib import Path
import argparse, hashlib, json, math, os, platform, subprocess, sys, wave
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'vendor/scoreweft'
sys.path.insert(0, str(BASE))
from bgm_gen.engines import run_engine
SR, FPS, N, SCALE = 48000, 30, 1440000, 2**23
PIN = 'aa5e7781952418f5881c41f166f237dc7a24111c'
PALETTE = {
 'nylon': {'program':'nylon','params':{'bright':.42,'pick':.23,'body':.6,'decay_ref':1.8},'gain_db':-4.,'pan':-.14},
 'bass': {'program':'bass','params':{},'gain_db':-21.,'pan':0.},
 'kick': {'program':'kick','params':{},'gain_db':-26.,'pan':0.},
 'motif': {'program':'fm_ep','params':{'ratio':1.,'idx':.70,'bright_decay':.055},'gain_db':-17.,'pan':.14},
 'knock': {'program':'fm_ep','params':{'ratio':2.71,'idx':1.05,'bright_decay':.025},'gain_db':-15.5,'pan':0.},
}
ANCHORS = {'json_origin':18,'arrangement_read':135,'first_hit':228,'rewrite':339,
 'old_point_pass':411,'new_hit':462,'result_carry':510,'result_settle':606,
 'brand_transfer':720,'brand_lock':774,'end':900}

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,d):
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def ff(args,data=None):
 r=subprocess.run(['ffmpeg','-nostdin','-y','-hide_banner','-nostats','-threads','1']+args,input=data,capture_output=True,timeout=120)
 if r.returncode: raise RuntimeError(r.stderr.decode(errors='replace')[-3000:])
 return r
def decode(p): return np.frombuffer(ff(['-loglevel','error','-i',str(p),'-f','f32le','-']).stdout,dtype='<f4').reshape(-1,2).copy()
def wav24(p,x,frames=N):
 assert x.shape==(frames,2) and np.max(x)<SCALE and np.min(x)>=-SCALE
 z=x.reshape(-1);raw=np.empty((len(z),3),np.uint8)
 raw[:,0]=z&255;raw[:,1]=(z>>8)&255;raw[:,2]=(z>>16)&255
 p.parent.mkdir(parents=True,exist_ok=True)
 with wave.open(str(p),'wb') as w:
  w.setnchannels(2);w.setsampwidth(3);w.setframerate(SR);w.writeframes(raw.tobytes())
def score():
 ev=[]
 def add(voice,t,p,d=.28,v=62,g=0,phrase='',anchor=None):
  s=round(t*SR); row={'id':f'e{len(ev):03}','voice':voice,'track':'timeline_cue' if voice=='knock' else 'bed',
   'sample':s,'seconds':s/SR,'pitch':p,'duration_seconds':d,'velocity':v,'gain_db':g,
   'pan':PALETTE[voice]['pan'],'phrase':phrase}
  if anchor: row['anchor']=anchor
  ev.append(row)
 def pl(t,p,v=62,d=.28,g=0,phrase=''): add('nylon',t,p,d,v,g,phrase)
 def bass(t,p=38,v=58,d=.32): add('bass',t,p,d,v,phrase='intermittent grounding')
 def kick(t,v=58): add('kick',t,36,1.2,v,phrase='sparse low pulse')
 # New phrase form, not a crop or time stretch of the 120s work.
 for t,p,v,d in [(.12,50,48,.6),(.60,57,47,.5),(1.40,64,43,.7),(2.80,69,43,.55),(3.60,62,56,.25),(3.86,66,51,.22)]:
  pl(t,p,v,d,phrase='JSON source / open question')
 bass(.60,38,43,.55)
 for t,p,v in [(4.1,55,61),(4.35,62,48),(4.85,64,58),(5.1,69,53),(5.60,57,63),(5.85,66,50),(6.1,62,56),(6.28,69,43)]:
  pl(t,p,v,phrase='three materials / light offbeat motion')
 for t,p,v in [(4.1,31,53),(5.1,38,48),(6.1,38,46)]: bass(t,p,v)
 for t,v in [(4.1,51),(5.1,45)]: kick(t,v)
 # Waiting stays exposed. This exact one-shot occurs only twice.
 for a in ['first_hit','new_hit']: add('knock',ANCHORS[a]/FPS,74,.12,80,phrase='same arranged content',anchor=a)
 for t,p,v in [(7.83,62,64),(8.10,66,50),(8.35,69,58),(8.85,64,49),(9.1,62,53)]:
  pl(t,p,v,phrase='first hit / music answers after the content')
 bass(7.85,38,59,.4);kick(7.85,56)
 add('motif',8.60,78,.26,52,phrase='gentle musical answer, not a cue')
 # Density drops before the rewrite. No new attack around the old empty point.
 for t,p,v,d in [(9.95,57,45,.65),(10.6,64,40,.55),(11.3,61,40,.4),(12.0,66,36,.7),(14.5,69,31,.18)]:
  pl(t,p,v,d,-3,phrase='rewrite / removed pulse / waiting')
 # Re-entry begins after the second content hit, never on the result-carry anchor.
 for t,p,v in [(15.68,62,66),(15.93,66,51),(16.18,69,61),(16.68,64,52),(16.9,66,44)]:
  pl(t,p,v,phrase='new arrangement / lifted answer')
 bass(15.68,38,61,.4);kick(15.68,57);bass(16.43,45,49,.25)
 for t,p,v in [(17.50,55,62),(17.75,62,50),(18.0,64,56),(18.35,69,48),(18.75,57,65),(19.0,61,52),(19.25,64,59),(19.63,69,49),(20.0,62,57)]:
  pl(t,p,v,phrase='carried result / brief moving phrase')
 for t,p,v in [(17.5,31,58),(18.75,33,60),(19.5,38,53)]:bass(t,p,v)
 for t,v in [(17.75,51),(18.75,55),(19.5,43)]:kick(t,v)
 for t,p,v in [(18.1,78,47),(18.60,76,43),(19.10,74,48)]:add('motif',t,p,.3,v,phrase='short F-sharp E D answer')
 for t,p,v,d in [(20.35,66,43,.8),(21.20,57,40,.7),(22.10,62,41,.75),(23.0,64,38,.7),(23.65,69,36,.5)]:
  pl(t,p,v,d,-2,phrase='settled relation / readable result')
 bass(21.20,38,39,.65)
 # Brand transfers via a small descending answer, then one warm final sonority.
 for t,p,v in [(24.15,74,45),(24.50,69,40),(24.9,66,37)]:add('motif',t,p,.32,v,-1,phrase='brand transfer / descending answer')
 for t,p,v,d in [(25.30,50,48,2.7),(25.37,57,43,2.6),(25.44,62,40,2.5),(25.51,66,35,2.4),(25.58,71,30,2.3)]:
  pl(t,p,v,d,-1,phrase='single final D6/9 / natural tail')
 bass(25.3,38,44,.9)
 ev.sort(key=lambda x:(x['sample'],x['id']))
 assert sum(e['track']=='timeline_cue' for e in ev)==2
 assert all(0<=e['sample']<N for e in ev)
 return {'format':'project-exact-sample-score/1','title':'One timeline / 30 seconds','duration_seconds':30,
  'format_audio':{'sr':SR,'channels':2,'sample_frames':N,'picture_fps':FPS},'anchors_frames':ANCHORS,
  'palette':PALETTE,'events':ev,'notation_bpm_reference':120,'events_are_absolute_samples':True,
  'source_pin':PIN,'listening_status':'UNRUN','independent_review_status':'UNRUN_ADAPTED_PRODUCTION_INPUTS',
  'voice_processing':{'nylon':'Project attack 0.30 to 1 smoothstep, 55ms below MIDI55, 40ms otherwise; low nylon below55 receives -3dB event gain. Not an engine edit.'},
  'cue_contract':'Same source, pitch, duration, gain, pan and processing at frame228 and462; no source event at411 or510. Bed tails allowed.',
  'render_contract':'Unit velocity100 one-shots; event velocity/100 linear post gain; equal-power pan; common static gain plus optional linked no-delay peak gain; two PCM24 stems quantized first, mix exact integer sum.'}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
 out=args.out.resolve();out.mkdir(parents=True,exist_ok=True);(out/'evidence').mkdir(exist_ok=True);(out/'one-shots').mkdir(exist_ok=True)
 (out/'source').mkdir(exist_ok=True);(out/'source/build.py').write_bytes(Path(__file__).read_bytes())
 score_data=score();dump(out/'score.json',score_data)
 prior=json.loads((BASE/'SOURCES.json').read_text())
 before={p:sha(BASE/p) for p in prior['files']}

 if prior['commit'] != PIN or before != prior['files']: raise RuntimeError('Vendored source integrity mismatch')
 dump(out/'evidence/source-before.json',{'pin':PIN,'sha256':before})
 dump(out/'evidence/environment.json',{'python':sys.version,'numpy':np.__version__,'platform':platform.platform(),
  'ffmpeg':subprocess.run(['ffmpeg','-version'],capture_output=True,text=True,check=True).stdout.splitlines()[0],
  'source_pin':PIN,'sequential':True,'runtime_override':{'diagnostic':True},'downloaded':False,'installed':False,'mac_access':False})
 bank={};bankmeta=[]
 for e in score_data['events']:
  key=(e['voice'],e['pitch'],e['duration_seconds'])
  if key in bank:continue
  c=PALETTE[key[0]];ident=json.dumps([key,c['program'],c['params']],sort_keys=True,separators=(',',':'))
  token=hashlib.sha256(ident.encode()).hexdigest()[:20];p=out/'one-shots'/f'{token}.wav'
  spec={'engine':'synth','spec_version':'1','sr':SR,'channels':2,'out':str(p),
   'payload':{'program':c['program'],'params':c['params'],'notes':[[key[1],0,key[2]*2,100]],'bpm':120}}
  result=run_engine(spec,runtime={'diagnostic':True});a=decode(result['path'])[:,0]
  assert np.isfinite(a).all() and np.abs(a).max()<1
  a[:16]*=np.linspace(0,1,16,dtype=np.float32)
  if key[0]=='nylon':
   n=round((.055 if key[1]<55 else .040)*SR);t=np.linspace(0,1,n,dtype=np.float32)
   a[:n]*=.30+.70*t*t*(3-2*t)
  bank[key]=a
  bankmeta.append({'identity':token,'key':key,'spec':spec,'source_sha256':sha(p),
   'processed_float32_sha256':hashlib.sha256(a.astype('<f4').tobytes()).hexdigest(),'sample_frames':len(a)})
 dump(out/'one-shot-bank.json',bankmeta)
 tracks={k:np.zeros((N,2),np.float32) for k in ['bed','timeline_cue']};placements=[]
 for e in score_data['events']:
  a=bank[(e['voice'],e['pitch'],e['duration_seconds'])];start=e['sample'];end=min(N,start+len(a))
  beyond=a[end-start:];assert not np.count_nonzero(beyond),e
  c=PALETTE[e['voice']];gain=10**((c['gain_db']+e['gain_db']+(-3 if e['voice']=='nylon' and e['pitch']<55 else 0))/20)*e['velocity']/100
  z=a[:end-start]*gain;angle=(e['pan']+1)*math.pi/4
  tracks[e['track']][start:end,0]+=z*math.cos(angle);tracks[e['track']][start:end,1]+=z*math.sin(angle)
  nz=np.flatnonzero(np.abs(z)>1e-8)
  placements.append({'id':e['id'],'track':e['track'],'anchor':e.get('anchor'),'scheduled_sample':start,
   'first_sample_above_1e8_relative':int(nz[0]) if len(nz) else None,'nonzero_after_film':int(np.count_nonzero(beyond))})
 # Safety tail only; all authored notes already end naturally before this.
 fade=np.ones(N,np.float32);a,b=round(29.5*SR),round(29.9*SR)
 fade[a:b]=.5+.5*np.cos(np.linspace(0,math.pi,b-a));fade[b:]=0
 for x in tracks.values():x*=fade[:,None]
 # Native integration keeps the actual short content as an independently timed
 # source. It is not baked into the bed or disguised as a single 0s cue event.
 native=out/'native-inputs';native.mkdir(exist_ok=True)
 wav24(native/'bed-premaster.wav',np.rint(tracks['bed']*SCALE).astype(np.int32))
 cue_frames=34;cue_n=cue_frames*1600;s1=ANCHORS['first_hit']*1600;s2=ANCHORS['new_hit']*1600
 q1=tracks['timeline_cue'][s1:s1+cue_n];q2=tracks['timeline_cue'][s2:s2+cue_n]
 assert np.array_equal(q1,q2)
 wav24(native/'content-cue.wav',np.rint(q1*SCALE).astype(np.int32),frames=cue_n)
 dump(out/'native-binding-fragment.json',{
  'status':'fragment_only_merge_into_verified_full_timeline_then_validate_and_run_native_audio',
  'sources':[{'id':'timeline_music_bed','path':'native-inputs/bed-premaster.wav'},
             {'id':'same_content_cue','path':'native-inputs/content-cue.wav'}],
  'audio_tracks':[{'name':'bed','duck':False,'events':[{'src':'timeline_music_bed','in':0,'out':900,'volume':1.0}]},
    {'name':'timeline_cue','duck':False,'events':[
     {'src':'same_content_cue','src_in':0,'in':228,'out':262,'volume':1.0},
     {'src':'same_content_cue','src_in':0,'in':462,'out':496,'volume':1.0}]}],
  'note':'Paths relative to this fragment location. Rebase if copied. These premaster sources need the real native audio stage; audition master is a separate output identity. Do not combine mastered bed with this unmastered cue.'})
 dump(out/'evidence/placements.json',placements)
 after={p:sha(BASE/p) for p in before}
 if before != after: raise RuntimeError('Vendored source changed during synthesis')
 dump(out/'evidence/source-after.json',{'pin':PIN,'sha256':after,'unchanged':True})
 dump(out/'evidence/input-identities.json',{'build_script_sha256':sha(__file__),
  'motion_plan_sha256':sha(ROOT/'motion-plan.json'),'score_sha256':sha(out/'score.json'),
  'native_audio_stage':'UNRUN','human_listening':'UNRUN',
  'output_sha256':{p.name:sha(p) for p in sorted(native.glob('*.wav'))}})
 print(json.dumps({'output':str(out),'events':len(score_data['events']),'bank_count':len(bank),
  'native_audio_stage':'UNRUN','human_listening':'UNRUN'},indent=2),flush=True)

if __name__=='__main__': main()
