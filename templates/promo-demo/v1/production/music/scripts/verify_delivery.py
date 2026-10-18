#!/usr/bin/env python3
"""Author verification; independent review remains a separate step."""
from pathlib import Path
import argparse, hashlib, json, math, re, subprocess, wave
import numpy as np
ROOT=Path(__file__).resolve().parents[1];SR=48000;N=5760000;SPF=1600;S=2**23

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')
def read(p):
 with wave.open(str(p),'rb') as w:
  assert (w.getframerate(),w.getnchannels(),w.getsampwidth(),w.getnframes())==(SR,2,3,N)
  b=np.frombuffer(w.readframes(N),np.uint8).reshape(-1,3)
 a=b[:,0].astype(np.int32)|(b[:,1].astype(np.int32)<<8)|(b[:,2].astype(np.int32)<<16)
 a=(a^(1<<23))-(1<<23)
 return a.reshape(-1,2)
def write(p,a):
 z=a.reshape(-1);b=np.empty((z.size,3),np.uint8);b[:,0]=z&255;b[:,1]=(z>>8)&255;b[:,2]=(z>>16)&255
 with wave.open(str(p),'wb') as w:w.setnchannels(2);w.setsampwidth(3);w.setframerate(SR);w.writeframes(b.tobytes())
def db(a):return 20*math.log10(max(float(a),1e-15))
def measure(p,log):
 r=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-nostats','-i',str(p),'-af','ebur128=peak=true','-f','null','-'],capture_output=True,text=True,timeout=180,check=True)
 log.write_text(r.stderr);s=r.stderr[r.stderr.rindex('Summary:'):]
 return dict(lufs=float(re.search(r'I:\s*(-?[\d.]+)\s*LUFS',s)[1]),true_peak_db=float(re.search(r'Peak:\s*(-?[\d.]+)',s)[1]))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--build',type=Path,required=True);a=ap.parse_args();b=a.build;ev=b/'evidence';audio=b/'audio'
 score=json.loads((ROOT/'score.json').read_text());cues=json.loads((ROOT/'inputs/cues.json').read_text())
 roles={r:read(audio/(r+'.wav')) for r in ['T17','T18','T19','T20']};mix=read(audio/'mix.wav');s=sum(roles.values())
 assert np.array_equal(s,mix)
 assert np.max(np.abs(mix))<S and np.count_nonzero(mix[-4800:])==0
 env=json.loads((audio/'waveform-S5.json').read_text());assert env['provenance']['sha256']==sha(audio/'mix.wav')
 assert env['provenance']['pcm_sha256']==hashlib.sha256(mix.astype('<i4').tobytes()).hexdigest()
 assert len(env['samples'])==480
 for j,z in enumerate(env['samples']):
  f=1680+j;sl=mix[f*SPF:(f+1)*SPF];assert z['frame']==f and z['sample_start']==f*SPF and z['sample_end']==(f+1)*SPF
  assert z['min']==int(sl.min())/S and z['max']==int(sl.max())/S and z['value']==int(np.abs(sl).max())/S
 cue_events={e['picture_cue']:e for e in score['events'] if e.get('picture_cue')}
 for c in cues['contacts']:
  if c['audio_role']=='T19':assert cue_events[c['id']]['sample']==c['sample_at_48khz']
 # No note attacks in protected full-world hold except its arrival sonority.
 assert not [e for e in score['events'] if 2706<=e['frame']<2760]
 assert not [e for e in score['events'] if 2010<e['frame']<2100]
 # Numeric phrase variation check only, not a claim of artistic quality.
 signatures=[]
 for k in range(60):
  rows=[[e['voice'],e['sample']-k*96000,e['pitch'],e['duration_seconds'],e['velocity'],e['gain_db']] for e in score['events'] if k*96000<=e['sample']<(k+1)*96000]
  signatures.append(hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest())
 four=[tuple(signatures[i:i+4]) for i in range(57)];assert len(set(four))==len(four)
 out=b/'removal-controls';out.mkdir(exist_ok=True);controls={}
 for role,(lo,hi) in zip(roles,[(300,600),(810,960),(0,180),(190,264)]):
  x=mix[lo*SPF:hi*SPF];stem=roles[role][lo*SPF:hi*SPF];minus=x-stem
  write(out/(role+'-full.wav'),x);write(out/(role+'-solo.wav'),stem);write(out/(role+'-removed.wav'),minus)
  controls[role]=dict(global_frame_window=[lo,hi],sample_frames=len(x),removed_difference_rms_dbfs=db(np.sqrt(np.mean((stem.astype(np.float64)/S)**2))),
   listening_status='NOT_LISTENED_BY_AUTHOR; ready for independent/user audition')
 # AAC diagnostic is not the video or the delivered lossless master.
 aac=b/'diagnostic-aac-192k.m4a'
 subprocess.run(['ffmpeg','-nostdin','-y','-hide_banner','-loglevel','error','-i',str(audio/'mix.wav'),'-c:a','aac','-b:a','192k',str(aac)],check=True,timeout=180)
 aacqc=measure(aac,ev/'diagnostic-aac-ebur128.log')
 # Plot actual per-frame RMS and min/max envelope as a standalone SVG, same fixed scale.
 x=mix.astype(np.float64)/S;frames=x.reshape(3600,SPF,2);rms=np.sqrt(np.mean(frames**2,axis=(1,2)));peak=np.max(np.abs(frames),axis=(1,2))
 p=' '.join(f'{50+i/3:.2f},{220-v*180:.2f}' for i,v in enumerate(peak));q=' '.join(f'{50+i/3:.2f},{220-v*180:.2f}' for i,v in enumerate(rms))
 scene_lines=''.join(f'<line x1="{50+z[0]/3:.1f}" y1="45" x2="{50+z[0]/3:.1f}" y2="220" stroke="#bbb"/><text x="{54+z[0]/3:.1f}" y="40">{z[1]}</text>' for z in [(0,'S1'),(300,'S2'),(720,'S3'),(1200,'S4'),(1680,'S5'),(2160,'S6'),(2760,'S7'),(3240,'S8')])
 svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1300 280"><rect width="1300" height="280" fill="#f5f2e9"/><g font-family="sans-serif" font-size="14" fill="#333"><text x="50" y="20">Actual delivered PCM: frame peak (orange), stereo RMS (green); fixed full-scale = 1</text>{scene_lines}<polyline points="{p}" fill="none" stroke="#d87945" stroke-width="1"/><polyline points="{q}" fill="none" stroke="#285f50" stroke-width="1.5"/><text x="50" y="250">0 s</text><text x="1210" y="250">120 s</text><text x="50" y="271">{sha(audio/"mix.wav")}</text></g></svg>'
 (ev/'actual-PCM-dynamics.svg').write_text(svg)
 stats=dict(stem_sum_bit_exact=True,format_all_exact=True,waveform_all_480_bins_exact=True,fixed_tactile_cues_exact=True,
  four_bar_signatures_unique=True,bar_signatures=signatures,removal_controls=controls,aac_diagnostic=aacqc,
  aac_diagnostic_pass=aacqc['true_peak_db']<=-1.2 and -15<=aacqc['lufs']<=-13,
  caveat='AAC diagnostic does not substitute for final rendered video post-encode measurement; no actual listening performed.')
 dump(ev/'author-verification.json',stats);print(json.dumps({k:v for k,v in stats.items() if k!='bar_signatures'},indent=2))
if __name__=='__main__':main()
