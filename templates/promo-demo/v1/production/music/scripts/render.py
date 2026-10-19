#!/usr/bin/env python3
"""Single-job bounded CPU production of original four-role score.
Run through the reviewed external render_guard.py, never launch parallel renders.
"""
from pathlib import Path
import argparse, hashlib, json, math, os, platform, re, shutil, subprocess, sys, wave
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
BASE=None  # Explicit --scoreweft-root is required; no bundled engine or network lookup.
SR=48000; N=5760000; SPF=1600; PIN='aa5e7781952418f5881c41f166f237dc7a24111c'; SCALE=2**23
ROLES=['T17','T18','T19','T20']
def dump(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()
def ff(args, data=None, timeout=180):
 p=subprocess.run(['ffmpeg','-nostdin','-y','-hide_banner','-nostats','-threads','1']+args,input=data,capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-3000:])
 return p

def decode(path):
 p=ff(['-loglevel','error','-i',str(path),'-f','f32le','-'])
 return np.frombuffer(p.stdout,dtype='<f4').reshape(-1,2).copy()
def wavfloat(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 ff(['-loglevel','error','-f','f32le','-ar',str(SR),'-ac','2','-i','-','-c:a','pcm_f32le',str(p)],np.asarray(x,dtype='<f4').tobytes())
def wav24(p,x):
 """Write exact signed PCM24 integer frames without dither or normalisation."""
 a=np.asarray(x,dtype=np.int32)
 assert a.shape==(N,2) and np.max(a)<SCALE and np.min(a)>=-SCALE
 raw=np.empty((a.size,3),np.uint8);z=a.reshape(-1)
 raw[:,0]=z&255;raw[:,1]=(z>>8)&255;raw[:,2]=(z>>16)&255
 p.parent.mkdir(parents=True,exist_ok=True)
 with wave.open(str(p),'wb') as w:w.setnchannels(2);w.setsampwidth(3);w.setframerate(SR);w.writeframes(raw.tobytes())
def db(x):return 20*math.log10(max(float(x),1e-15))
def loud(path,log):
 p=ff(['-i',str(path),'-af','loudnorm=I=-14.5:TP=-2.0:LRA=11:print_format=json','-f','null','-'])
 t=p.stderr.decode();log.parent.mkdir(parents=True,exist_ok=True);log.write_text(t)
 d=json.loads(t[t.index('{'):t.rindex('}')+1])
 return {k:float(v) for k,v in d.items() if k.startswith('input_')}
def ebu(path,log):
 p=ff(['-i',str(path),'-af','ebur128=peak=true','-f','null','-']);t=p.stderr.decode();log.write_text(t)
 s=t[t.rindex('Summary:'):]
 return dict(lufs=float(re.search(r'I:\s*(-?[\d.]+)\s*LUFS',s)[1]),true_peak_db=float(re.search(r'Peak:\s*(-?[\d.]+)',s)[1]),lra_lu=float(re.search(r'LRA:\s*([\d.]+)',s)[1]))
def linked_limiter(x,ceiling_db=-3.2,release_ms=110):
 """Project mix gain, not an engine patch. 1ms peak blocks, 6ms lookahead.
 Min of current/next 6 blocks, instant attenuation and 110ms exponential
 dB release, interpolated from block starts. Conservative forward envelope.
 A common sample-domain safety ratio caps numerical overshoot without moving audio.
 """
 peak=np.abs(x).max(axis=1);m=peak.reshape(-1,48).max(axis=1);ceiling=10**(ceiling_db/20)
 pad=np.pad(m,(0,6));look=np.lib.stride_tricks.sliding_window_view(pad,7).max(axis=1)[:len(m)]
 target=np.minimum(0.,20*np.log10(ceiling/np.maximum(look,1e-15)))
 gain_db=np.empty_like(target);current=0.;decay=math.exp(-.001/(release_ms/1000))
 for j,v in enumerate(target):current=min(v,current*decay);gain_db[j]=current
 gain=np.interp(np.arange(N),np.arange(len(m))*48,10**(gain_db/20)).astype(np.float32)
 gain=np.minimum(gain,ceiling/np.maximum(peak,1e-15))
 return gain

def source_manifest():
 # Read only the explicit audited source set, never repository metadata or unrelated files.
 declared=json.loads((ROOT/'SOURCE_INTEGRITY.json').read_text())['files']
 files={}
 runtime_files={str(p.relative_to(BASE)) for p in (BASE/'bgm_gen').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
 expected_runtime={name for name in declared if name.startswith('bgm_gen/')}
 assert runtime_files==expected_runtime, 'Pinned runtime inventory differs; use a clean exact source checkout'
 for name in sorted(declared):
  relative=Path(name)
  assert not relative.is_absolute() and '..' not in relative.parts
  resolved=(BASE/relative).resolve();resolved.relative_to(BASE)
  assert resolved.is_file(),f'Missing pinned Scoreweft source: {name}'
  files[name]=sha(resolved)
 return files

def render(out,score_path,reuse=None):
 out.mkdir(parents=True,exist_ok=True);evdir=out/'evidence';evdir.mkdir(exist_ok=True)
 score=json.loads(score_path.read_text());palette=score['palette']
 assert score['format_audio']['sample_frames']==N
 before=source_manifest();old=json.loads((ROOT/'SOURCE_INTEGRITY.json').read_text())
 for p,v in old['files'].items():assert before[p]==v['sha256'],p
 dump(evdir/'source-integrity-before.json',dict(commit=PIN,path=str(BASE),files=before))
 inputs={str(p.relative_to(ROOT)):sha(p) for p in [ROOT/'score.json',ROOT/'scoreweft-editable-projection.json',ROOT/'inputs/cues.json',ROOT/'inputs/audio-roles.json',Path(__file__),ROOT/'scripts/compose.py']}
 dump(evdir/'input-sha256.json',inputs)
 # Validate the secondary standard projection without modifying or rendering it.
 load_project(str(ROOT/'scoreweft-editable-projection.json'))
 dump(evdir/'environment.json',dict(scoreweft_commit=PIN,python=sys.version,numpy=np.__version__,platform=platform.platform(),
  ffmpeg=subprocess.run(['ffmpeg','-version'],capture_output=True,text=True,check=True).stdout.splitlines()[0],
  runtime_override={'diagnostic':True},new_packages=False,network_assets=False,sequential=True,
  source_route='Unchanged run_engine synth -> 48k unit one-shots -> exact-sample project scheduler -> linked common mix gain -> PCM24'))
 # Banks are original synthesized one-shots, not imported samples.
 cache={};bankmeta=[];onedir=out/'one-shots';onedir.mkdir(exist_ok=True)
 specs={}
 for event in score['events']:
  k=(event['voice'],event['pitch'],event['duration_seconds'])
  if k in specs:continue
  c=palette[k[0]];identity=json.dumps([k,c['program'],c['params']],sort_keys=True,separators=(',',':'))
  token=hashlib.sha256(identity.encode()).hexdigest()[:20]
  spec=dict(engine='synth',spec_version='1',sr=SR,channels=2,out=str(onedir/(token+'.wav')),
   payload=dict(program=c['program'],params=c['params'],notes=[[k[1],0,k[2]*2,100]],bpm=120))
  specs[k]=(token,spec)
 dump(out/'one-shot-specs.json',{token:spec for token,spec in specs.values()})
 if reuse:
  old_specs=json.loads((reuse/'one-shot-specs.json').read_text())
  old_banks={r['identity']:r for r in json.loads((reuse/'one-shot-bank.json').read_text())}
  old_integrity=json.loads((reuse/'evidence/source-integrity-before.json').read_text())
  assert old_integrity['commit']==PIN and old_integrity['files']==before
 for i,(key,(token,spec)) in enumerate(specs.items()):
  if reuse:
   old_spec=dict(old_specs[token]);current_spec=dict(spec);old_spec.pop('out');current_spec.pop('out')
   assert old_spec==current_spec
   src=reuse/'one-shots'/(token+'.wav');assert sha(src)==old_banks[token]['source_sha256']
   shutil.copyfile(src,spec['out']);result={'path':spec['out']}
  else:
   result=run_engine(spec,runtime={'diagnostic':True})
  a=decode(Path(result['path']))[:,0]
  assert np.isfinite(a).all() and np.max(np.abs(a))<1
  # The native synth already fades its note. Preserve whole waveform, including zero tail.
  # Only the first 16 samples receive a tiny zero-start ramp against resampling pre-ringing.
  a[:16]*=np.linspace(0,1,16,dtype=np.float32)
  processing=score.get('voice_processing',{}).get(key[0])
  if processing:
   recovery_ms=processing['attack_recovery_ms_below55' if key[1]<55 else 'attack_recovery_ms_atleast55']
   count=min(len(a),round(recovery_ms*SR/1000));t=np.linspace(0,1,count,dtype=np.float32)
   smooth=t*t*(3-2*t);a[:count]*=processing['attack_start_gain']+(1-processing['attack_start_gain'])*smooth
  cache[key]=a
  bankmeta.append(dict(identity=token,voice=key[0],pitch=key[1],duration_seconds=key[2],source_sha256=sha(Path(result['path'])),processed_float32_sha256=hashlib.sha256(a.astype('<f4').tobytes()).hexdigest(),project_processing=processing,samples=len(a),first_sample=float(a[0])))
  if i%20==0:print(f'One-shots {i+1}/{len(specs)}',flush=True)
 dump(out/'one-shot-bank.json',bankmeta)
 stems={r:np.zeros((N,2),np.float32) for r in ROLES};placement=[]
 for e in score['events']:
  c=palette[e['voice']];a=cache[(e['voice'],e['pitch'],e['duration_seconds'])]
  start=e['sample'];end=min(N,start+len(a));over=a[end-start:]
  assert np.count_nonzero(over)==0,('nonzero tail beyond film',e)
  angle=(e['pan']+1)*math.pi/4
  gain=10**((c['gain_db']+e['gain_db'])/20)*e['velocity']/100
  z=a[:end-start]*gain
  stems[e['role']][start:end,0]+=z*math.cos(angle)
  stems[e['role']][start:end,1]+=z*math.sin(angle)
  nz=np.flatnonzero(np.abs(z)>1e-8)
  placement.append(dict(id=e['id'],role=e['role'],voice=e['voice'],picture_cue=e.get('picture_cue'),frame=e['frame'],
   scheduled_sample=start,first_nonzero_relative_sample=int(nz[0]) if len(nz) else None,
   zero_samples_before_schedule=True,nonzero_tail_beyond_film=int(np.count_nonzero(over))))
 del cache
 # Deliberate in-window tail polish: no hard truncation. Already-complete synth tails are preserved.
 fade=np.ones(N,np.float32);a=round(118.5*SR);b=round(119.9*SR)
 fade[a:b]=.5+.5*np.cos(np.linspace(0,math.pi,b-a));fade[b:]=0
 for x in stems.values():x*=fade[:,None]
 del fade
 raw=sum(stems.values());wavfloat(out/'premaster.wav',raw)
 base=loud(out/'premaster.wav',evdir/'premaster-loudnorm.log');target=-14.5;gain_db=target-base['input_i']
 passes=[]
 # Sequential calibration of mix only, no repeated synthesis and no dynamics flattening target.
 for iteration in range(8):
  scalar=10**(gain_db/20);candidate=raw*scalar;linked=linked_limiter(candidate)
  candidate*=linked[:,None];wavfloat(out/f'master-calibration-{iteration}.wav',candidate)
  measured=loud(out/f'master-calibration-{iteration}.wav',evdir/f'master-calibration-{iteration}.log')
  passes.append(dict(iteration=iteration,static_gain_db=gain_db,measurements=measured,max_gain_reduction_db=-db(np.min(linked))))
  error=target-measured['input_i']
  if abs(error)<=.12:break
  gain_db+=error
 assert abs(measured['input_i']-target)<=.4,measured
 assert measured['input_tp']<=-1.9,measured
 trim_db=score['render_contract']['final_delivery_trim_db']
 common=(linked*scalar*10**(trim_db/20)).astype(np.float32);np.save(out/'common-master-gain.npy',common)
 del linked,candidate,raw
 mix=np.zeros((N,2),np.int32);stemstats={}
 for role in ROLES:
  x=stems[role];x*=common[:,None]
  ints=np.rint(x*SCALE).astype(np.int32);wav24(out/'audio'/f'{role}.wav',ints);mix+=ints
  stemstats[role]=dict(sample_frames=N,sr=SR,channels=2,encoding='pcm_s24le',sha256=sha(out/'audio'/f'{role}.wav'),
   peak_dbfs=db(np.max(np.abs(ints))/SCALE),rms_dbfs=db(np.sqrt(np.mean((ints.astype(np.float64)/SCALE)**2))),
   nonzero_interleaved_samples=int(np.count_nonzero(ints)),first=list(map(int,ints[0])),last=list(map(int,ints[-1])))
  del ints
 wav24(out/'audio/mix.wav',mix)
 ff(['-loglevel','error','-i',str(out/'audio/mix.wav'),'-c:a','flac',str(out/'audio/mix.flac')])
 assert np.array_equal(decode(out/'audio/mix.wav'),decode(out/'audio/mix.flac'))
 final=ebu(out/'audio/mix.wav',evdir/'mix-ebur128.log');lm=loud(out/'audio/mix.wav',evdir/'mix-loudnorm.log')
 assert -15<=final['lufs']<=-13 and final['true_peak_db']<=-1.2,final
 dump(evdir/'master-recipe.json',dict(premaster=base,calibration_passes=passes,final=final,loudnorm_input_statistics=lm,
  final_delivery_trim_db=trim_db,one_shot_reuse_from=str(reuse) if reuse else None,common_gain_sha256=sha(out/'common-master-gain.npy'),method='Linked 1ms-block peak control with 6ms lookahead and 110ms dB release; static LUFS calibration; identical per-sample gain for all four stems; no time shift. PCM24 stems quantized first; mix is exact integer sum.',
  target_lufs=-14.5,sample_peak_ceiling_db=-3.2,quiet_exception='Frames 2010–2100 intentionally low density; source-reading and final tail intentionally sparse.'))
 dump(evdir/'placements.json',placement)
 dump(evdir/'stems.json',stemstats)
 # Envelope belongs to the exact delivered unity stem sum, never the audition or isolated stem.
 pcm_hash=hashlib.sha256(mix.astype('<i4').tobytes()).hexdigest();mixsha=sha(out/'audio/mix.wav')
 bins=[]
 for frame in range(1680,2160):
  sl=mix[frame*SPF:(frame+1)*SPF];lo=int(sl.min());hi=int(sl.max());val=max(abs(lo),abs(hi))/SCALE
  bins.append(dict(frame=frame,sample_start=frame*SPF,sample_end=(frame+1)*SPF,min=lo/SCALE,max=hi/SCALE,value=val))
 envelope=dict(format='framivra-waveform/1',provenance=dict(kind='actual-pcm',sha256=mixsha,source='audio/mix.wav',
  pcm_sha256=pcm_hash,pcm_hash_representation='little-endian signed int32 interleaved, containing sign-extended signed PCM24 values',
  sample_rate=SR,channels=2,sample_frames=N,sample_width_bits=24,mix_recipe_sha256=sha(evdir/'master-recipe.json')),
  analysis_frame_window=[1680,2160],analysis_sample_window=[2688000,3456000],bins=480,
  amplitude_mapping=dict(kind='stereo-channel-union',fixed_scale=1,definition='min/max signed PCM24 divided by 8388608. value=max(abs(min),abs(max)); full scale is always 1. No per-bin or excerpt normalization.'),samples=bins)
 dump(out/'audio/waveform-S5.json',envelope)
 # Compact final mix QC, scene dynamics, role removal controls and mono compatibility.
 y=mix.astype(np.float64)/SCALE; mid=y.mean(axis=1);side=(y[:,0]-y[:,1])/2
 scenes=[]
 for s in score['scenes']:
  z=y[s['start_frame']*SPF:s['end_frame']*SPF]
  scenes.append(dict(**s,rms_dbfs=db(np.sqrt(np.mean(z*z))),peak_dbfs=db(np.abs(z).max()),events=sum(s['start_frame']<=e['frame']<s['end_frame'] for e in score['events'])))
 controls={}
 for role,(a,b) in zip(ROLES,[(300,600),(810,960),(0,180),(190,264)]):
  z=decode(out/'audio'/f'{role}.wav')[a*SPF:b*SPF]
  controls[role]=dict(frame_window=[a,b],removal_difference_rms_dbfs=db(np.sqrt(np.mean(z*z))),peak_dbfs=db(np.abs(z).max()),nonzero_samples=int(np.count_nonzero(z)),listening_status='NOT_PERFORMED')
 native_50ms=np.sqrt(np.mean(y.reshape(-1,2400,2)**2,axis=(1,2)));silent=native_50ms<.001
 longest=cur=0
 for q in silent:cur=cur+1 if q else 0;longest=max(longest,cur)
 qc=dict(status='TECHNICAL_AUTHOR_CHECKS_PASS; INDEPENDENT_REVIEW_AND_LISTENING_PENDING',sample_frames=N,sample_rate=SR,channels=2,duration_seconds=120,
  mix_sha256=mixsha,flac_sha256=sha(out/'audio/mix.flac'),pcm_sha256=pcm_hash,flac_pcm_equal=True,
  loudness=final,loudnorm_input_statistics=lm,peak_dbfs=db(np.max(np.abs(y))),rms_dbfs=db(np.sqrt(np.mean(y*y))),
  full_scale_samples=int(np.count_nonzero(np.abs(y)>=1)),nonfinite_samples=int(np.count_nonzero(~np.isfinite(y))),
  first_sample=list(map(int,mix[0])),last_sample=list(map(int,mix[-1])),last_100ms_nonzero_samples=int(np.count_nonzero(mix[-4800:])),
  longest_native_50ms_silence_below_minus60_dbfs_seconds=longest*.05,
  left_right_correlation=float(np.corrcoef(y.T)[0,1]),mono_average_loss_db=db(np.sqrt(np.mean(mid*mid)))-db(np.sqrt(np.mean(y*y))),side_mid_ratio_db=db(np.sqrt(np.mean(side*side)))-db(np.sqrt(np.mean(mid*mid))),
  scenes=scenes,removal_controls=controls,role_stats=stemstats,whole_film_event_count=len(score['events']),
  protected_windows={name:dict(frame_window=[a,b],rms_dbfs=db(np.sqrt(np.mean(y[a*SPF:b*SPF]**2))),peak_dbfs=db(np.abs(y[a*SPF:b*SPF]).max())) for name,a,b in [('quiet_glide',2010,2100),('world_register',2700,2760),('source_reading',2760,3240),('climax',2400,2520),('final_resolution',3480,3600)]})
 dump(evdir/'qc.json',qc)
 after=source_manifest();assert before==after
 dump(evdir/'source-integrity-after.json',dict(commit=PIN,unchanged=True,files=after))
 dump(out/'delivery.json',dict(status='RENDERED_FOR_INDEPENDENT_REVIEW_AND_USER_LISTENING',audio=[dict(path=str(p.relative_to(out)),sha256=sha(p),bytes=p.stat().st_size,sample_rate=SR,channels=2,sample_frames=N) for p in sorted((out/'audio').glob('*')) if p.suffix in ['.wav','.flac']],
  waveform=dict(path='audio/waveform-S5.json',sha256=sha(out/'audio/waveform-S5.json')),score_sha256=sha(score_path),guard_required=True))
 print(json.dumps(dict(output=str(out),loudness=final,protected_windows=qc['protected_windows'],scenes=scenes),indent=2),flush=True)

def main():
 global BASE, run_engine, load_project
 ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--scoreweft-root',type=Path,required=True);ap.add_argument('--score',type=Path,default=ROOT/'score.json');ap.add_argument('--reuse-one-shots',type=Path);args=ap.parse_args()
 BASE=args.scoreweft_root.resolve()
 assert BASE.is_dir(),f'Scoreweft root does not exist: {BASE}'
 # Verify pinned bytes BEFORE importing external runtime code.
 actual=source_manifest();expected=json.loads((ROOT/'SOURCE_INTEGRITY.json').read_text())
 assert expected['commit']==PIN
 for name,record in expected['files'].items():assert actual[name]==record['sha256'],f'Unpinned Scoreweft source: {name}'
 sys.path.insert(0,str(BASE))
 from bgm_gen.engines import run_engine
 from bgm_gen.core.project import load_project
 render(args.out.resolve(),args.score.resolve(),args.reuse_one_shots.resolve() if args.reuse_one_shots else None)
if __name__=='__main__':main()
