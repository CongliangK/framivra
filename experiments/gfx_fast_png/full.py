"""Run the real vtx all pipeline with an experimental Browser factory injection.
Each arm generates its own synthetic assets and cache. No production opt-in added.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from experiments.gfx_fast_png.bench import CASES, CONTRACT, html, rss_bytes
from vtx import gfx_render as gfx


def worker(timeline, fast):
    browser_type=gfx.Browser
    class ExperimentalBrowser(browser_type):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs,fast_png=fast)
    gfx.Browser=ExperimentalBrowser
    from vtx import cli
    sys.argv=['vtx','-t',str(timeline),'all']
    sys.exit(cli.main() or 0)


def run(root,fast,ffmpeg,frames=12):
    root.mkdir(parents=True,exist_ok=False)
    asset=root/'source.mp4'
    subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','color=c=white:s=1920x1080:r=30:d=3',
                    '-f','lavfi','-i','sine=frequency=330:sample_rate=48000:duration=3',
                    '-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-c:a','aac',
                    '-ac','2','-shortest',str(asset)],check=True,capture_output=True)
    clips=[]
    for i,case in enumerate(CASES):
        name=case+'.html';(root/name).write_text(html(case))
        clips.append(dict(src=name,dst_in=3+i*frames,dur=frames))
    timeline=root/'timeline.json'
    timeline.write_text(json.dumps(dict(version=2,project=dict(fps=30,width=1920,height=1080,duration_frames=90),
        sources=[dict(id='source',path=str(asset))],video_tracks=[dict(name='base',kind='aroll',
        clips=[dict(src='source',src_in=0,src_out=90,dst_in=0,dur=90)]),dict(name='gfx',kind='gfx',clips=clips)],
        subtitles=[],audio_tracks=[dict(name='voice',events=[dict(src='source',**{'in':0,'out':90},volume=.3)])])))
    env=os.environ.copy();env.update(VTX_GFX='cdp',VTX_GFX_STATIC='0',VTX_GFX_NAVFREE='0',VTX_GFX_PNG_LEVEL='3',VTX_CHROME='/usr/bin/google-chrome',VTX_GFX_PAGES='1',
        VTX_GFX_TABS='1',VTX_GFX_CACHE_NS='issue64_fastpng',VTX_GFX_SPLIT_FRAMES='0',VTX_VIDEO_SEGMENTS='1',VTX_IO_WORKERS='1')
    env['PATH']=str(Path(ffmpeg).parent)+os.pathsep+env['PATH']
    command=[sys.executable,__file__,'--worker',str(timeline)]+(['--fast'] if fast else [])
    peak=[0];stop=threading.Event()
    def sample():
        while not stop.wait(.02):peak[0]=max(peak[0],rss_bytes())
    thread=threading.Thread(target=sample);thread.start();start=time.perf_counter()
    try:
        r=subprocess.run(command,env=env,capture_output=True)
        wall=time.perf_counter()-start
    finally:
        stop.set();thread.join()
    (root/'all.log').write_bytes(r.stdout+r.stderr)
    from vtx.infra import Ctx
    from vtx.hygiene import gfx_dir
    from unittest.mock import patch
    hashes={}
    with patch.dict(os.environ, {'VTX_GFX_CACHE_NS':'issue64_fastpng'}):
        ctx=Ctx(timeline)
        for clip in clips:
            for png in gfx_dir(ctx,clip).glob('f*.png'):
                hashes[clip['src']+'/'+png.name]=hashlib.sha256(gfx.png_decode_rgba(png.read_bytes()).tobytes()).hexdigest()
    final=root/'out/final.mp4'
    result=dict(fast=fast,exit=r.returncode,all_wall_s=wall,peak_process_tree_rss_bytes=peak[0],
                png_bytes=sum(p.stat().st_size for p in (root/'cache/gfx_exp/issue64_fastpng').rglob('f*.png')),
                final_bytes=final.stat().st_size if final.exists() else None,
                rgba_hashes=hashes,command=command)
    (root/'result.json').write_text(json.dumps(result,indent=2))
    print(root.name,result['exit'],wall,flush=True)
    if r.returncode:raise RuntimeError('vtx all failed: '+str(root/'all.log'))
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path);p.add_argument('--ffmpeg',default='/usr/bin/ffmpeg')
    p.add_argument('--smoke',action='store_true');p.add_argument('--worker',type=Path);p.add_argument('--fast',action='store_true');p.add_argument('--dry',action='store_true');a=p.parse_args()
    if a.worker:return worker(a.worker,a.fast)
    contract=dict(CONTRACT,all_duration_frames=90,pages=1,split_frames=0,video_segments=1,io_workers=1,
                  full_vtx_pipeline_ms='measured as all_wall_s',twoshot='naturally supported override in full; forced fallback only standalone matrix')
    if a.dry:print(json.dumps(contract,indent=2));return
    a.output.mkdir(parents=True,exist_ok=False);(a.output/'contract.json').write_text(json.dumps(contract,indent=2))
    if a.smoke:
        run(a.output/'smoke',a.fast,a.ffmpeg,frames=1)
        return
    results=[]
    for round_number in range(1,4):
        pair={}
        for fast in ([False,True] if round_number!=2 else [True,False]):
            result=run(a.output/f'round-{round_number}'/('fast' if fast else 'normal'),fast,a.ffmpeg)
            results.append(result);pair[fast]=result
        # Content keys include independent root paths. Compare ordered full-frame hashes by source order later.
        if pair[False]['rgba_hashes'] != pair[True]['rgba_hashes'] or len(pair[False]['rgba_hashes']) != 84:
            raise RuntimeError('Full pipeline gfx RGBA mismatch')
    (a.output/'results.json').write_text(json.dumps(results,indent=2))

if __name__=='__main__':main()
