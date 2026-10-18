"""Opt-in synthetic color diagnostic; not a full compositor/QC acceptance test."""
import argparse,hashlib,json,pathlib,subprocess,sys,types
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from vtx.encoders import pick_vcodec
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=pathlib.Path,required=True,help='new evidence directory; synthetic files only')
P=parser.parse_args().output
P.mkdir(parents=True,exist_ok=False)
commands=[]
def run(args):
 r=subprocess.run(args,capture_output=True); commands.append({'argv':args,'rc':r.returncode,'stderr':r.stderr.decode(errors='replace')});
 if r.returncode: raise RuntimeError(commands[-1])
 return r.stdout
def sha(b):return hashlib.sha256(b).hexdigest()
W,H=1920,1080
colors=[(0,0,0),(255,255,255),(255,0,0),(0,255,0),(0,0,255),(128,128,128),(255,255,0),(0,255,255)]
(P/'bars1080.rgb').write_bytes(b''.join(bytes(c)*(W//8) for c in colors)*H)
base=['ffmpeg','-y','-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{W}x{H}','-framerate','30','-i',str(P/'bars1080.rgb')]
results={'version':run(['ffmpeg','-version']).decode(),'matrix':{},'encoders':{}}
raw={}
for name,vf in [('implicit','format=yuv420p'),('601','scale=out_color_matrix=bt601:out_range=tv,format=yuv420p'),('709','scale=out_color_matrix=bt709:out_range=tv,format=yuv420p')]:
 b=run(base+['-vf',vf,'-frames:v','1','-f','rawvideo','-pix_fmt','yuv420p','-']);raw[name]=b
 results['matrix'][name]={'sha256':sha(b),'Y':[b[W//16+i*(W//8)] for i in range(8)],'U':[b[W*H+W//32+i*(W//16)] for i in range(8)],'V':[b[W*H*5//4+W//32+i*(W//16)] for i in range(8)]}
results['matrix']['implicit_equals_601']=raw['implicit']==raw['601'];results['matrix']['implicit_equals_709']=raw['implicit']==raw['709']
for name,draft,gpu,no_nv in [('x264-medium',False,False,False),('x264-veryfast',True,False,True),('nvenc-p4',True,False,False),('nvenc-p7',False,True,False)]:
 import os
 if no_nv:os.environ['VTX_NO_NVENC']='1'
 else:os.environ.pop('VTX_NO_NVENC',None)
 args,label=pick_vcodec(types.SimpleNamespace(dry=True,gpu=gpu),draft)
 dest=P/(name+'.mp4');run(base+['-frames:v','1']+args+[str(dest)])
 probe=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(dest)]))
 decoded=run(['ffmpeg','-v','error','-i',str(dest),'-f','rawvideo','-pix_fmt','yuv420p','-'])
 vui_args=['ffmpeg','-v','verbose','-i',str(dest),'-c:v','copy','-bsf:v','trace_headers','-f','null','-']
 vui=subprocess.run(vui_args,capture_output=True)
 commands.append({'argv':vui_args,'rc':vui.returncode,'stderr':vui.stderr.decode(errors='replace')})
 if vui.returncode: raise RuntimeError(commands[-1])
 (P/(name+'-vui.txt')).write_bytes(vui.stderr)
 mux=P/(name+'-mux.mp4');run(['ffmpeg','-y','-v','error','-i',str(dest),'-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.033333333','-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-movflags','+faststart',str(mux)])
 muxdecode=run(['ffmpeg','-v','error','-i',str(mux),'-map','0:v:0','-f','rawvideo','-pix_fmt','yuv420p','-'])
 muxprobe=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(mux)]))
 results['encoders'][name]={'args':args,'label':label,'probe':probe,'decoded_sha256':sha(decoded),'decoded_Y':[decoded[W//16+i*(W//8)] for i in range(8)],'mux_probe':muxprobe,'mux_decoded_equal':decoded==muxdecode,'decoded_frames':len(decoded)//(W*H*3//2)}
(P/'results.json').write_text(json.dumps(results,indent=2));(P/'commands.json').write_text(json.dumps(commands,indent=2))
print(json.dumps({'matrix':results['matrix'],'encoders':{k:{a:v[a] for a in ('args','decoded_Y','mux_decoded_equal','decoded_frames')} for k,v in results['encoders'].items()}},indent=2))
