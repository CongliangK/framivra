"""Bounded synthetic fast-PNG experiment. Writes only an explicitly new evidence root.
No production cache keys, default flags, timers or asset directories are modified.
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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vtx import gfx_render as gfx

CONTRACT = dict(width=1920, height=1080, frames=12, rounds=3, tabs=1,
                rss_budget_bytes=4 * 1024**3, order=['normal-fast', 'fast-normal', 'normal-fast'],
                cdp_call_ms=None, base64_transport_ms=None, base64_decode_ms=None,
                full_vtx_pipeline_ms=None,
                telemetry_reason='Issue #63 event interface is absent at this baseline; no substitute production timer.')
CASES = ['low-entropy', 'high-entropy-blur', 'gradient', 'transparent', 'flatten', 'twoshot', 'canvas']


def html(case):
    bg = 'transparent' if case in ('transparent', 'twoshot', 'canvas') else '#eee'
    js = '''const f=+(new URLSearchParams(location.search).get('f')||0);
const c=document.querySelector('canvas'),x=c.getContext('2d');
let seed=123456;function rand(){seed=(1664525*seed+1013904223)>>>0;return seed>>>24;}
'''
    if case == 'opaque':
        js += 'x.fillStyle="#123456";x.fillRect(0,0,1920,1080);'
    elif case == 'high-entropy-blur':
        js += 'let d=x.createImageData(1920,1080);for(let i=0;i<d.data.length;i+=4){d.data[i]=rand();d.data[i+1]=rand();d.data[i+2]=rand();d.data[i+3]=255;}x.putImageData(d,0,0);c.style.filter="blur(2px)";'
    elif case == 'gradient':
        js += 'let g=x.createLinearGradient(0,0,1920,1080);g.addColorStop(0,"#123456");g.addColorStop(1,"#fedcba");x.fillStyle=g;x.fillRect(0,0,1920,1080);'
    else:
        js += 'x.fillStyle="rgba(200,30,100,0.65)";x.fillRect(150+f*3,120,1200,800);'
    if case == 'canvas':
        js += 'window.__vtx_export_png=()=>c.toDataURL("image/png");'
    return '<style>html,body{margin:0;background:'+bg+'}canvas{display:block}</style><canvas width="1920" height="1080"></canvas><script>'+js+'</script>'


def rss_bytes():
    # Aggregate live process tree; /proc sampling includes Chrome children, not ru_maxrss.
    parents, rss = {}, {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry/'status').read_text()
            fields = dict(line.split(':',1) for line in status.splitlines() if ':' in line)
            parents[int(entry.name)] = int(fields['PPid'])
            rss[int(entry.name)] = int(fields.get('VmRSS','0 kB').split()[0])*1024
        except (OSError, ValueError, KeyError):
            pass
    descendants = {os.getpid()}
    while True:
        more = {pid for pid, parent in parents.items() if parent in descendants}
        if more <= descendants:
            return sum(rss.get(pid,0) for pid in descendants)
        descendants |= more


def run(root, case, fast, ffmpeg):
    root.mkdir(parents=True, exist_ok=False)
    page=root/'page.html'; page.write_text(html(case))
    browser_type = gfx.Browser
    tab_call = gfx.Tab.call
    capabilities=[]
    class ExperimentalBrowser(browser_type):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs,fast_png=fast)
        def open_tabs(self):
            tabs=super().open_tabs()
            capabilities.extend(dict(requested=t.fast_png_requested,supported=t.fast_png_supported,
                                     active=t.fast_png_active) for t in tabs)
            return tabs
    def call(tab, method, **kwargs):
        if case in ('twoshot', 'opaque') and method == 'Emulation.setDefaultBackgroundColorOverride':
            raise RuntimeError('Experiment explicitly forces override unavailable to exercise twoshot')
        return tab_call(tab,method,**kwargs)
    peak=[0]; stop=threading.Event()
    def sample():
        while not stop.wait(.02):
            peak[0]=max(peak[0],rss_bytes())
    thread=threading.Thread(target=sample); thread.start()
    start=time.perf_counter()
    try:
        with patch.object(gfx,'Browser',ExperimentalBrowser),patch.object(gfx.Tab,'call',call):
            stats=gfx.render_page(str(page),str(root/'frames'),CONTRACT['frames'],
                                  chrome_path='/usr/bin/google-chrome',tabs=1,width=1920,height=1080)
        gfx_s=time.perf_counter()-start
        if stats['failed'] or stats['rendered'] != CONTRACT['frames']:
            raise RuntimeError('Incomplete gfx output: '+repr(stats))
        pngs=sorted((root/'frames').glob('*.png'))
        hashes=[hashlib.sha256(gfx.png_decode_rgba(p.read_bytes()).tobytes()).hexdigest() for p in pngs]
        ffstart=time.perf_counter()
        command=[ffmpeg,'-v','error','-threads','1','-framerate','30','-i',str(root/'frames/f%04d.png'),
                 '-f','rawvideo','-pix_fmt','rgba','-threads','1','-y',os.devnull]
        subprocess.run(command,check=True,capture_output=True)
        ff_s=time.perf_counter()-ffstart
        result=dict(case=case,fast=fast,capabilities=capabilities,stats=stats,gfx_wall_s=gfx_s,
                    ffmpeg_decode_wall_s=ff_s,bounded_gfx_decode_wall_s=gfx_s+ff_s,
                    png_bytes=sum(p.stat().st_size for p in pngs),rgba_sha256=hashes,
                    forced_override_unavailable=case in ('twoshot', 'opaque'),ffmpeg_command=command)
    finally:
        stop.set();thread.join()
    result['peak_process_tree_rss_bytes']=peak[0]
    (root/'result.json').write_text(json.dumps(result,indent=2))
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--ffmpeg',required=True);p.add_argument('--cases',nargs='+',choices=CASES+['opaque'],default=CASES);p.add_argument('--dry',action='store_true');a=p.parse_args()
    if a.dry:
        print(json.dumps(dict(contract=CONTRACT,cases=a.cases),indent=2));return
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'contract.json').write_text(json.dumps(dict(contract=CONTRACT,cases=a.cases),indent=2))
    results=[]
    for round_number in range(3):
        for case in a.cases:
            pair={}
            for fast in ([False,True] if round_number != 1 else [True,False]):
                label='fast' if fast else 'normal'
                result=run(a.output/f'round-{round_number+1}'/case/label,case,fast,a.ffmpeg)
                results.append(result);pair[fast]=result
                print(round_number+1,case,label,result['gfx_wall_s'],flush=True)
            if pair[False]['rgba_sha256'] != pair[True]['rgba_sha256']:
                raise RuntimeError('RGBA mismatch: '+case)
    (a.output/'results.json').write_text(json.dumps(results,indent=2))

if __name__=='__main__':main()
