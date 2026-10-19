#!/usr/bin/env python3
"""Bind S5 waveform to an exact exported music or native Framivra PCM file.
No resampling, remixing, normalization, trimming or gain change occurs here.
"""
from pathlib import Path
import argparse, hashlib, json, subprocess
import numpy as np

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def main():
 a=argparse.ArgumentParser();a.add_argument('--pcm',required=True,type=Path);a.add_argument('--out',required=True,type=Path)
 a.add_argument('--stage',required=True,choices=['music-export','framivra-audio-stage','final-video-decoded-pcm']);a.add_argument('--source-label',required=True)
 a.add_argument('--recipe',type=Path);args=a.parse_args();p=args.pcm.resolve()
 meta=json.loads(subprocess.run(['ffprobe','-v','error','-select_streams','a','-show_streams','-of','json',str(p)],capture_output=True,text=True,check=True,timeout=30).stdout)
 assert len(meta['streams'])==1,'Require one explicit PCM audio source';m=meta['streams'][0]
 assert int(m['sample_rate'])==48000 and m['channels']==2,(m['sample_rate'],m['channels'])
 assert m['codec_name'].startswith('pcm_'),'Input must be decoded PCM, not an encoded media stream'
 r=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-i',str(p),'-map','0:a:0','-c:a','pcm_f32le','-f','f32le','-'],capture_output=True,check=True,timeout=120)
 x=np.frombuffer(r.stdout,dtype='<f4').reshape(-1,2)
 assert x.shape==(5760000,2),x.shape
 assert np.isfinite(x).all() and np.max(np.abs(x))<=1
 samples=[]
 for f in range(1680,2160):
  q=x[f*1600:(f+1)*1600];mn=float(q.min());mx=float(q.max())
  samples.append(dict(frame=f,sample_start=f*1600,sample_end=(f+1)*1600,min=mn,max=mx,value=max(abs(mn),abs(mx))))
 provenance=dict(kind='actual-pcm',stage=args.stage,sha256=sha(p),source=args.source_label,source_codec=m['codec_name'],
  pcm_sha256=hashlib.sha256(r.stdout).hexdigest(),pcm_hash_representation='native-rate decoded interleaved little-endian float32; no resampling or gain change',
  sample_rate=48000,channels=2,sample_frames=5760000)
 if args.recipe:provenance['mix_recipe_sha256']=sha(args.recipe)
 env=dict(format='framivra-waveform/1',provenance=provenance,analysis_frame_window=[1680,2160],analysis_sample_window=[2688000,3456000],bins=480,
  amplitude_mapping=dict(kind='stereo-channel-union',fixed_scale=1,definition='min/max of native decoded PCM across both channels; value=max(abs(min),abs(max)); full scale always 1. No per-bin or excerpt normalization.'),samples=samples)
 args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(env,indent=2)+'\n')
 print(json.dumps(dict(waveform=str(args.out),waveform_sha256=sha(args.out),provenance=provenance)))
if __name__=='__main__':main()
