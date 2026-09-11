"""Compare a warm rebuild after input edits with an independent cold rebuild."""
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('--repo',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--ffmpeg',required=True,type=Path);p.add_argument('--chrome',required=True);a=p.parse_args()
root=a.output.resolve();root.mkdir(parents=True,exist_ok=False);repo=a.repo.resolve();ff=a.ffmpeg.resolve();env=os.environ.copy();env.update(PYTHONUTF8='1',VTX_VIDEO_SEGMENTS='2',VTX_GFX_TABS='1',VTX_CHROME=a.chrome,TEMP=str(root),TMP=str(root));env['PATH']=str(ff.parent)+os.pathsep+env['PATH']
warm=root/'warm';warm.mkdir();(warm/'assets').mkdir()
def command(cmd,log):
    r=subprocess.run(list(map(str,cmd)),capture_output=True,env=env);(root/log).write_bytes(r.stdout+r.stderr)
    if r.returncode:raise RuntimeError(log)
    return r.stdout

def source(color,hz):
    command([ff,'-v','error','-y','-f','lavfi','-i',f'color={color}:s=1920x1080:r=30:d=6','-f','lavfi','-i',f'sine=frequency={hz}:sample_rate=48000:duration=6','-c:v','libx264','-preset','veryfast','-c:a','aac','-ac','2',warm/'assets/source.mp4'],f'generate-{hz}.txt')
source('gray',330)
for folder,color in [('a','white'),('b','yellow')]:
    (warm/folder).mkdir();(warm/folder/'same.html').write_text(f'''<body style="margin:0;background:transparent"><canvas id="c" width="1920" height="1080"></canvas><script>let x=c.getContext('2d');x.fillStyle='{color}';x.fillRect(100,100,1000,400);window.__vtx_export_png=()=>c.toDataURL();</script>''')
tl={'version':2,'project':{'fps':30,'width':1920,'height':1080,'duration_frames':180},'sources':[{'id':'a','path':'assets/source.mp4'}],
'video_tracks':[{'name':'base','kind':'aroll','clips':[{'src':'a','src_in':0,'src_out':180,'dst_in':0,'dur':180}]},{'name':'gfx','kind':'gfx','clips':[{'src':'a/same.html','dst_in':20,'dur':15},{'src':'b/same.html','dst_in':130,'dur':15}]}],
'subtitles':[{'in':40,'out':120,'text':'BEFORE'}],'audio_tracks':[{'name':'voice','events':[{'src':'a','in':0,'out':180,'volume':1.0}]}]}
(warm/'timeline.json').write_text(json.dumps(tl))
def render(project,label):
    for stage in ['validate','prepare','gfx','video','audio','mux']:
        command([sys.executable,'-X','utf8',repo/'vtx/vtx.py','-t',project/'timeline.json',stage],label+'-'+stage+'.txt')
render(warm,'initial')
original=(warm/'out/final.mp4').read_bytes()
# Same path, same duration; restore timestamps deliberately.
st=(warm/'assets/source.mp4').stat();source('navy',660);os.utime(warm/'assets/source.mp4',ns=(st.st_atime_ns,st.st_mtime_ns))
tl['subtitles'][0]['text']='AFTER';(warm/'timeline.json').write_text(json.dumps(tl))
p=warm/'a/same.html';st=p.stat();p.write_text(p.read_text().replace('white','lime'));os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns))
cold=root/'cold';cold.mkdir()
for name in ['assets','a','b']:shutil.copytree(warm/name,cold/name)
shutil.copy2(warm/'timeline.json',cold/'timeline.json')
render(warm,'warm');render(cold,'cold')
def pixels(project):
    return np.frombuffer(command([ff,'-v','error','-i',project/'out/final.mp4','-vf','scale=160:90','-pix_fmt','rgb24','-f','rawvideo','-'],project.name+'.rgb'),dtype=np.uint8).astype(float)
def audio(project):
    return command([ff,'-v','error','-i',project/'out/final.mp4','-vn','-f','s16le','-'],project.name+'.pcm')
x,y=pixels(warm),pixels(cold);diff=np.abs(x-y).reshape(180,-1).mean(axis=1)
report={'max_frame_mean_difference':float(diff.max()),'audio_equal':audio(warm)==audio(cold),'output_changed':original!=(warm/'out/final.mp4').read_bytes(),'same_named_pages_have_separate_cache':len(list((warm/'cache').rglob('gfx-*')))>1}
report['pass']=report['max_frame_mean_difference']<.1 and report['audio_equal'] and report['output_changed'] and report['same_named_pages_have_separate_cache']
(root/'results.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));sys.exit(0 if report['pass'] else 1)
