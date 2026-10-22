"""Synthetic end-to-end regression: python run_e2e.py --repo REPO --output NEW_DIR --ffmpeg FFMPEG --chrome CHROME.
Output must be new to prevent cache reuse. No private assets required.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='backslashreplace')
import numpy as np
# 沙箱兼容：经包装器启动时 sys.executable 可能指向包装器（如 *.AppImage）而非
# 真实解释器——回退 PATH 解析，保证子进程可执行（可 VTX_TEST_PYTHON 覆盖）。
PYTHON=os.environ.get('VTX_TEST_PYTHON') or (sys.executable if not sys.executable.endswith('.AppImage') else shutil.which('python3'))
p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--output',required=True);p.add_argument('--ffmpeg',required=True);p.add_argument('--chrome',required=True);a=p.parse_args()
repo=Path(a.repo).resolve();root=Path(a.output).resolve();root.mkdir(parents=True,exist_ok=False)
ff=Path(a.ffmpeg).resolve();env=os.environ.copy();env.update(PYTHONUTF8='1',VTX_CHROME=a.chrome,VTX_GFX_TABS='2',VTX_IO_WORKERS='2',TEMP=str(root),TMP=str(root));env['PATH']=str(ff.parent)+os.pathsep+env['PATH']
results=[]
cache_checks={}
def run(cmd,log,extra=None):
    e=env.copy();e.update(extra or {});t=time.monotonic()
    r=subprocess.run([str(x) for x in cmd],capture_output=True,env=e,timeout=900)
    Path(log).write_bytes(r.stdout+r.stderr)
    results.append({'step':str(Path(log).relative_to(root)),'exit':r.returncode,'seconds':round(time.monotonic()-t,3)})
    print(results[-1],flush=True);return r
assets=root/'素材 # inputs';assets.mkdir()
for name,color,hz in [('a','0x708090',330),('b','0x906040',550)]:
    r=run([ff,'-v','error','-y','-f','lavfi','-i',f'color={color}:s=1920x1080:r=30:d=6,drawbox=x=100:y=100:w=400:h=200:color=white:t=fill',
           '-f','lavfi','-i',f'sine=frequency={hz}:sample_rate=48000:duration=6','-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-c:a','aac','-ac','2','-shortest',assets/(name+'.mp4')],root/(name+'-generate.txt'))
    assert r.returncode==0
for case,segments in [('parallel',2),('single',1)]:
    project=root/(case+' 中文 # project');project.mkdir();(project/'gfx').mkdir()
    (project/'gfx/canvas #.html').write_text('''<html style="background:transparent"><body style="margin:0;background:transparent"><canvas id="c" width="1920" height="1080"></canvas><script>const x=c.getContext('2d'),f=Number(new URLSearchParams(location.search).get('f'));x.fillStyle='rgba(255,255,255,0.8)';x.fillRect(180+f*3,180,900,360);x.fillStyle='white';x.font='60px sans-serif';x.fillText('CANVAS '+f,240+f*3,360);window.__vtx_export_png=()=>c.toDataURL('image/png');</script></body></html>''',encoding='utf-8')
    (project/'gfx/dom #.html').write_text('''<html style="background:transparent"><body style="margin:0;background:transparent"><div style="position:absolute;left:180px;top:180px;width:1000px;height:360px;background:rgba(240,180,30,0.8);font:60px sans-serif;color:white;padding:40px;box-sizing:border-box">DOM FALLBACK</div></body></html>''',encoding='utf-8')
    tl={'version':2,'project':{'fps':30,'width':1920,'height':1080,'duration_frames':360},'sources':[{'id':n,'path':str(assets/(n+'.mp4'))} for n in ['a','b']],
        'video_tracks':[{'name':'base','kind':'aroll','clips':[{'src':n,'src_in':0,'src_out':180,'dst_in':i*180,'dur':180} for i,n in enumerate(['a','b'])]},
        {'name':'effects','kind':'gfx','clips':[{'src':'gfx/canvas #.html','dst_in':30,'dur':60},{'src':'gfx/dom #.html','dst_in':240,'dur':60}]}],
        'subtitles':[{'in':20,'out':100,'text':'CANVAS / subtitle test'},{'in':140,'out':230,'text':'SUBTITLE ACROSS SEGMENT'},{'in':245,'out':315,'text':'DOM / subtitle test'}],
        'audio_tracks':[{'name':'bed','events':[{'src':'a','in':0,'out':360,'loop':True,'volume':.3}]},{'name':'second','events':[{'src':'b','in':120,'out':270,'volume':.25}]}]}
    timeline=project/'timeline.json';timeline.write_text(json.dumps(tl,ensure_ascii=False,indent=2),encoding='utf-8')
    cmd=[PYTHON,'-X','utf8',repo/'vtx/vtx.py','-t',timeline]
    initial=run(cmd+['all'],root/(case+'-all.txt'),{'VTX_VIDEO_SEGMENTS':str(segments)})
    if initial.returncode == 0:
        cached={str(p.relative_to(project)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                for p in (project/'cache').rglob('*')
                if p.is_file() and p.suffix in ('.png', '.wav', '.mp4') and 'tmp' not in p.relative_to(project/'cache').parts}
        warm=run(cmd+['all'],root/(case+'-warm.txt'),{'VTX_VIDEO_SEGMENTS':str(segments)})
        cache_checks[case]={'files':len(cached),'unchanged':bool(cached) and all(
            p.is_file() and (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) == before
            for name,before in cached.items() for p in [project/name])}
    else:
        # Persist diagnostics even when the pipeline never produces final.mp4.
        # These are controlled synthetic-project logs already retained as artifacts.
        print('\n'.join((initial.stdout+initial.stderr).decode('utf-8',errors='replace').splitlines()[-60:]),flush=True)
        (root/'steps.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
        (root/'results.json').write_text(json.dumps({'pass':False,'steps':results,'failed_case':case},indent=2),encoding='utf-8')
        sys.exit(1)
    if (project/'out/final.mp4').exists():
        run(cmd+['--strict','qc'],root/(case+'-strict.txt'))
        run([ff,'-v','error','-i',project/'out/final.mp4','-f','null','-'],root/(case+'-decode.txt'))
        run([ff,'-hide_banner','-nostats','-i',project/'out/final.mp4','-af','ebur128=peak=true:framelog=quiet','-vf','blackdetect=d=0.01:pix_th=0.1','-f','null','-'],root/(case+'-signal.txt'))
        raw=run([ff,'-v','error','-i',project/'out/final.mp4','-vf','scale=160:90','-pix_fmt','rgb24','-f','rawvideo','-'],root/(case+'-frames.rgb'))
        # raw stdout above is intentionally retained as compact per-frame comparison evidence.
        assert raw.returncode==0
(root/'steps.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
frames=[]
for case in ['parallel','single']:
    b=(root/(case+'-frames.rgb')).read_bytes();frames.append(np.frombuffer(b,dtype=np.uint8).reshape(-1,90,160,3))
d=np.abs(frames[0].astype(float)-frames[1].astype(float)).mean(axis=(1,2,3))
checks={'cache':cache_checks,'same_frame_count':len(d)==360,'mean_rgb_difference':float(d.mean()),'max_frame_mean_difference':float(d.max()),'largest_difference_frame':int(d.argmax()),'comparison_pass':bool(d.max()<2)}
for case in ['parallel','single']:
    text=(root/(case+'-signal.txt')).read_text(errors='replace');peaks=re.findall(r'Peak:\s*(-?[\d.]+) dBFS',text)
    checks[case]={'no_black_intervals':'black_start:' not in text,'true_peak_dbfs':float(peaks[-1]) if peaks else None}
summary={'steps':results,'checks':checks,'source_sha256':{p.relative_to(repo/'vtx').as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((repo/'vtx').rglob('*.py'))}}
summary['pass']=all(c['unchanged'] for c in cache_checks.values()) and len(cache_checks)==2 and all(r['exit']==0 for r in results) and checks['comparison_pass'] and checks['same_frame_count'] and all(checks[c]['no_black_intervals'] and checks[c]['true_peak_dbfs'] is not None and checks[c]['true_peak_dbfs']<=-1 for c in ['parallel','single'])
(root/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(checks,indent=2))

if not summary["pass"]:sys.exit(1)
