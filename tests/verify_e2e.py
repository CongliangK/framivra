"""Additional full-resolution boundary and audio checks after run_e2e.py."""
import argparse,hashlib,json,subprocess
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--ffmpeg',default='ffmpeg');a=p.parse_args();root=a.root.resolve()
checks={};videos=[root/(c+' 中文 # project')/'out/final.mp4' for c in ['parallel','single']]
def decode(path,args):
    return subprocess.check_output([a.ffmpeg,'-v','error','-i',str(path),*args])
audio=[decode(v,['-vn','-ar','48000','-ac','2','-f','s16le','-']) for v in videos]
checks['audio_pcm_identical']=audio[0]==audio[1]
checks['audio_samples_per_channel']=len(audio[0])//4
checks['audio_duration_within_aac_padding']=abs(len(audio[0])//4-12*48000)<=1024
boundary={}
for n in [0,29,30,60,89,90,140,158,159,160,179,180,229,230,240,270,299,300,359]:
    data=[np.frombuffer(decode(v,['-vf',f'select=eq(n\\,{n})','-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-']),dtype=np.uint8).astype(float) for v in videos]
    boundary[n]=float(np.abs(data[0]-data[1]).mean())
checks['full_resolution_probes']=boundary;checks['full_resolution_comparison_pass']=max(boundary.values())<2
for n in [60,159,270]:
    subprocess.run([a.ffmpeg,'-v','error','-y','-i',str(videos[0]),'-vf',f'select=eq(n\\,{n}),scale=960:540','-frames:v','1',str(root/f'frame-{n}.png')],check=True)
checks['sha256']={str(v.relative_to(root)):hashlib.sha256(v.read_bytes()).hexdigest() for v in videos}
checks['pass']=checks['audio_pcm_identical'] and checks['audio_duration_within_aac_padding'] and checks['full_resolution_comparison_pass']
(root/'additional-checks.json').write_text(json.dumps(checks,indent=2),encoding='utf-8');print(json.dumps(checks,indent=2));assert checks['pass']
